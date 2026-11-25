#!/usr/bin/env python3
"""Push several files to the Meizu 15 repository in ONE commit, over the API.

Why: github.com:443 is unreliable from this network while api.github.com answers,
and api_push.py does one file per commit -- fifteen files meant fifteen commits
and fifteen ref updates. This does the same blob/tree/commit/ref dance with all
of them at once.

    export GH_TOKEN=...                 (or ~/.gh-token, or ../.gh-token)

    python3 scripts/ci/api_push_many.py "message" --changed
    python3 scripts/ci/api_push_many.py "message" path/a path/b ...
    python3 scripts/ci/api_push_many.py "message" --changed --dry-run

--changed takes what `git status --porcelain` reports (modified, added, deleted).
A deleted path is pushed as a tree entry with sha null, which removes it.

Nothing is force-pushed: the ref update uses force=false, so a concurrent commit
fails the push instead of being overwritten.
"""
import base64
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

OWNER, REPO = "1359352487-droid", "meizu15-unixnu"
ROOT = pathlib.Path(__file__).resolve().parents[2]


def token():
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if t:
        return t.strip()
    for p in (pathlib.Path.home() / ".gh-token", ROOT / ".gh-token"):
        if p.exists():
            return p.read_text(encoding="utf-8-sig").strip()
    sys.exit("no token: set GH_TOKEN or write ~/.gh-token")


def api(path, data=None, method=None, timeout=600):
    url = "https://api.github.com" + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url, data=body, method=method or ("POST" if data else "GET"),
        headers={"Authorization": "Bearer " + token(), "User-Agent": "meizu15",
                 "Accept": "application/vnd.github+json",
                 **({"Content-Type": "application/json"} if data else {})})
    return json.loads(urllib.request.build_opener().open(req, timeout=timeout).read())


def changed_paths():
    """Modified, added and deleted paths, relative to ROOT (repo root).

    -uall because the default only reports a new directory ("scripts/local/"),
    and the whole point is to upload the files inside it.
    """
    res = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=str(ROOT),
                         capture_output=True, text=True, check=True)
    out = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        status, path = line[:2], line[3:].strip()
        if " -> " in path:                       # renamed: take the destination
            path = path.split(" -> ", 1)[1]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if (ROOT / path).is_dir():               # a whole new directory
            for sub in sorted((ROOT / path).rglob("*")):
                if sub.is_file() and ".git" not in sub.parts:
                    out.append(str(sub.relative_to(ROOT)))
            continue
        out.append(path)
    return out


def push_many(paths, message, dry_run=False):
    entries = []
    for rel in paths:
        local = ROOT / rel
        if not local.exists():
            entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": None})
            print("delete  %s" % rel)
            continue
        print("blob    %-70s %8d B" % (rel, local.stat().st_size))
        entries.append({"path": rel, "mode": "100644", "type": "blob",
                        "sha": None if dry_run else api(
                            "/repos/%s/%s/git/blobs" % (OWNER, REPO),
                            {"content": base64.b64encode(local.read_bytes()).decode(),
                             "encoding": "base64"})["sha"]})
    if dry_run:
        print("dry run: %d path(s), nothing sent" % len(entries))
        return None

    ref = api("/repos/%s/%s/git/ref/heads/main" % (OWNER, REPO))
    parent = ref["object"]["sha"]
    base_tree = api("/repos/%s/%s/git/commits/%s" % (OWNER, REPO, parent))["tree"]["sha"]
    tree = api("/repos/%s/%s/git/trees" % (OWNER, REPO),
               {"base_tree": base_tree, "tree": entries})["sha"]
    commit = api("/repos/%s/%s/git/commits" % (OWNER, REPO),
                 {"message": message, "tree": tree, "parents": [parent]})
    api("/repos/%s/%s/git/refs/heads/main" % (OWNER, REPO),
        {"sha": commit["sha"], "force": False}, method="PATCH")
    print("pushed %d path(s) -> %s" % (len(entries), commit["sha"][:8]))
    return commit["sha"]


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    message = argv[1]
    rest = argv[2:]
    dry_run = "--dry-run" in rest
    rest = [a for a in rest if a != "--dry-run"]
    paths = changed_paths() if rest == ["--changed"] else rest
    if not paths:
        print("nothing to push")
        return 1
    push_many(paths, message, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
