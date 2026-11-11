#!/usr/bin/env python3
"""Push this repository to GitHub through the REST API, without git.

Useful where no git client is available or where a credential helper cannot run
interactively. Everything goes through the Git Data API: blobs -> tree -> commit
-> ref.

Requirements:
  - a fine-grained personal access token with Contents: Read and write
  - the token in ../.gh-token (one line, nothing else)
  - an EMPTY target repository (no README, no .gitignore, no license)

Usage:
    python3 scripts/push-api.py --repo <owner>/<name>
    python3 scripts/push-api.py --repo <owner>/<name> --dry-run

The token is read from disk, used in memory, and never written into the git
config, the remote URL or the commit.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from dulwich.repo import Repo

API = "https://api.github.com"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), ".gh-token")


def read_token() -> str:
    if not os.path.exists(TOKEN_FILE):
        raise SystemExit(f"no token file at {TOKEN_FILE}")
    tok = open(TOKEN_FILE, encoding="utf-8").read().strip()
    if not tok:
        raise SystemExit("token file is empty")
    return tok


def call(method: str, path: str, token: str, body=None, expect=(200, 201)):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "meizu15-port")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
        return e.code, payload


def collect(repo_path: str):
    """Return [(path, bytes, mode)] for every file in HEAD, in tree order."""
    repo = Repo(repo_path)
    head = repo.head()
    tree = repo[repo[head].tree]
    out = []

    def walk(t, prefix=b""):
        for name, mode, sha in t.iteritems():
            full = prefix + name
            if mode & 0o040000:
                walk(repo[sha], full + b"/")
            else:
                blob = repo[sha]
                m = "100755" if (mode & 0o111) else "100644"
                out.append((full.decode(), blob.data, m))

    walk(tree)
    out.sort(key=lambda x: x[0])
    return repo, out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="<owner>/<name> of the EMPTY target repository")
    ap.add_argument("--repo-path", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = read_token()
    local, files = collect(args.repo_path)
    print(f"local HEAD {local.head().decode()[:12]}, {len(files)} files, "
          f"{sum(len(f[1]) for f in files)/1024/1024:.2f} MB")

    st, me = call("GET", "/user", token)
    if st != 200:
        raise SystemExit(f"token rejected ({st}): {me}")
    login = me["login"]
    print(f"authenticated as: {login}")

    owner, name = args.repo.split("/", 1)
    st, repo_info = call("GET", f"/repos/{owner}/{name}", token)
    if st != 200:
        raise SystemExit(f"repository {args.repo} not reachable ({st}): {repo_info}")
    print(f"target: {repo_info['full_name']}  private={repo_info['private']}  "
          f"size={repo_info['size']} KB")

    # refuse to clobber an existing history
    st, refs = call("GET", f"/repos/{owner}/{name}/git/refs/heads", token)
    existing = refs if isinstance(refs, list) else []
    if existing:
        raise SystemExit(
            "target already has branches: " + ", ".join(r["ref"] for r in existing) +
            "\nThis script only initialises an empty repository. Delete the branch or "
            "use git push instead.")

    st, me_info = call("GET", f"/repos/{owner}/{name}/git/ref/heads/{args.branch}", token)
    if st == 200:
        raise SystemExit(f"branch {args.branch} already exists")

    if args.dry_run:
        print("dry run: token valid, target empty, would push", len(files), "files")
        return 0

    # 1. blobs
    tree_items = []
    for i, (path, data, mode) in enumerate(files, 1):
        st, blob = call("POST", f"/repos/{owner}/{name}/git/blobs", token,
                        {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        if st not in (200, 201):
            raise SystemExit(f"blob failed for {path} ({st}): {blob}")
        tree_items.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})
        if i % 25 == 0 or i == len(files):
            print(f"  blobs {i}/{len(files)}")

    # 2. tree
    st, tree = call("POST", f"/repos/{owner}/{name}/git/trees", token, {"tree": tree_items})
    if st not in (200, 201):
        raise SystemExit(f"tree failed ({st}): {tree}")
    print(f"tree {tree['sha'][:12]}")

    # 3. commit (reuse the local message so history matches the git one)
    msg = local[local.head()].message.decode("utf-8", "replace")
    st, commit = call("POST", f"/repos/{owner}/{name}/git/commits", token, {
        "message": msg,
        "tree": tree["sha"],
        "parents": [],
        "author": {"name": "Meizu15 port", "email": "port@example.invalid",
                   "date": datetime.datetime.fromtimestamp(
                       local[local.head()].commit_time or 0,
                       datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
    })
    if st not in (200, 201):
        raise SystemExit(f"commit failed ({st}): {commit}")
    print(f"commit {commit['sha'][:12]}")

    # 4. ref
    st, ref = call("POST", f"/repos/{owner}/{name}/git/refs", token,
                   {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]})
    if st not in (200, 201):
        raise SystemExit(f"ref failed ({st}): {ref}")
    print(f"branch {args.branch} -> {commit['sha'][:12]}")
    print(f"\nhttps://github.com/{owner}/{name}")
    print(f"https://github.com/{owner}/{name}/actions")

    # record the remote WITHOUT the token
    with local.get_config() as cfg:
        try:
            cfg.set((b"remote", b"origin"), b"url",
                    f"https://github.com/{owner}/{name}.git".encode())
            cfg.set((b"remote", b"origin"), b"fetch",
                    f"+refs/heads/*:refs/remotes/origin/*".encode())
        except Exception as e:
            print("could not write remote config:", e)
    print("remote 'origin' recorded (no credentials in it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
