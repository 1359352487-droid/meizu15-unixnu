#!/usr/bin/env python3
"""Push one file to the Meizu 15 repository through the GitHub API.

Why not git: github.com:443 is unreliable from this network, while api.github.com
answers. This talks only to the API -- blob, tree, commit, then move the ref.

    export GH_TOKEN=...            (or put the token in ~/.gh-token)
    python3 scripts/ci/api_push.py scripts/ci/apply_nonapple_guards.py "message"

The token needs `contents: write` (a classic PAT with `repo` also works).
"""
import base64
import json
import os
import pathlib
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


def push(rel_path, message):
    local = (ROOT / rel_path).read_bytes()
    ref = api("/repos/%s/%s/git/ref/heads/main" % (OWNER, REPO))
    parent = ref["object"]["sha"]
    blob = api("/repos/%s/%s/git/blobs" % (OWNER, REPO),
               {"content": base64.b64encode(local).decode(), "encoding": "base64"})
    commit = api("/repos/%s/%s/git/commits/%s" % (OWNER, REPO, parent))
    tree = api("/repos/%s/%s/git/trees" % (OWNER, REPO),
               {"base_tree": commit["tree"]["sha"],
                "tree": [{"path": rel_path, "mode": "100644",
                          "type": "blob", "sha": blob["sha"]}]})
    nc = api("/repos/%s/%s/git/commits" % (OWNER, REPO),
             {"message": message, "tree": tree["sha"], "parents": [parent]})
    api("/repos/%s/%s/git/refs/heads/main" % (OWNER, REPO),
        {"sha": nc["sha"], "force": False}, method="PATCH")
    print("pushed %s -> %s" % (rel_path, nc["sha"][:8]))
    return nc["sha"]


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    push(sys.argv[1], sys.argv[2])
