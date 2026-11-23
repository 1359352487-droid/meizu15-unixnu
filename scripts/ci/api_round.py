#!/usr/bin/env python3
"""One full round over the GitHub API only.

    export GH_TOKEN=...                 (or ~/.gh-token)
    python3 scripts/ci/api_round.py <commit-sha-that-touches-the-kernel>

Steps, in order:
  1. find the kernel run for <sha> and wait for its `meizu15-kernel` artifact
  2. download it, gunzip it, drop it into inputs/kernel.macho
  3. commit that through the API (a 16 MB blob upload)
  4. wait for the payload run of the new commit
  5. download `meizu15-uefi-payload-debug-full` into out/

It never calls git: github.com:443 is unreliable here, api.github.com is not.
After it finishes, flash out/boot-meizu15-payload-debug.img and photograph the
panel -- the top of the screen carries `last cp N`, which is what every round is
judged on.
"""
import base64
import gzip
import io
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
import zipfile

OWNER, REPO = "1359352487-droid", "meizu15-unixnu"
ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "out"

direct = urllib.request.build_opener()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


raw = urllib.request.build_opener(NoRedirect())


def token():
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if t:
        return t.strip()
    for p in (pathlib.Path.home() / ".gh-token", ROOT / ".gh-token"):
        if p.exists():
            return p.read_text(encoding="utf-8-sig").strip()
    sys.exit("no token: set GH_TOKEN or write ~/.gh-token")


TOK = token()


def api(path, data=None, method=None, timeout=600):
    url = "https://api.github.com" + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url, data=body, method=method or ("POST" if data else "GET"),
        headers={"Authorization": "Bearer " + TOK, "User-Agent": "meizu15",
                 "Accept": "application/vnd.github+json",
                 **({"Content-Type": "application/json"} if data else {})})
    return json.loads(direct.open(req, timeout=timeout).read())


def fetch_artifact(aid):
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/%s/actions/artifacts/%d/zip" % (OWNER, REPO, aid),
        headers={"Authorization": "Bearer " + TOK, "User-Agent": "meizu15"})
    try:
        return raw.open(req, timeout=180).read()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        return direct.open(urllib.request.Request(
            e.headers["Location"], headers={"User-Agent": "meizu15"}), timeout=900).read()


def find_run(workflow, sha, tries=40):
    for _ in range(tries):
        d = api("/repos/%s/%s/actions/workflows/%s/runs?per_page=8" % (OWNER, REPO, workflow))
        for w in d.get("workflow_runs", []):
            if (w["head_sha"] or "").startswith(sha):
                return w
        time.sleep(15)
    return None


def wait_artifact(run_id, needle, label, minutes=60):
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        arts = api("/repos/%s/%s/actions/runs/%d/artifacts" % (OWNER, REPO, run_id))
        for a in arts["artifacts"]:
            if needle in a["name"]:
                print("%s artifact %d (%d bytes)" % (label, a["id"], a["size_in_bytes"]), flush=True)
                return a
        print(time.strftime("%H:%M:%S"), "waiting for", label, flush=True)
        time.sleep(30)
    return None


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sha = sys.argv[1]

    run = find_run("build-meizu15-xnu-hakios-v2.yml", sha)
    if not run:
        sys.exit("no kernel run for " + sha)
    print("kernel run", run["id"], flush=True)
    art = wait_artifact(run["id"], "meizu15-kernel", "kernel", 70)
    if not art:
        sys.exit("no kernel artifact")

    z = zipfile.ZipFile(io.BytesIO(fetch_artifact(art["id"])))
    blob = None
    for n in z.namelist():
        d = z.read(n)
        if n.endswith(".gz"):
            d = gzip.decompress(d)
        if d[:4] == b"\xcf\xfa\xed\xfe":
            blob = d
    if blob is None:
        sys.exit("no Mach-O in the kernel artifact")

    dest = ROOT / "inputs" / "kernel.macho"
    old = dest.read_bytes()
    print("kernel.macho %d -> %d changed=%s" % (len(old), len(blob), old != blob), flush=True)
    dest.write_bytes(blob)
    if old == blob:
        print("kernel unchanged, stopping", flush=True)
        return

    ref = api("/repos/%s/%s/git/ref/heads/main" % (OWNER, REPO))
    parent = ref["object"]["sha"]
    nb = api("/repos/%s/%s/git/blobs" % (OWNER, REPO),
             {"content": base64.b64encode(blob).decode(), "encoding": "base64"})
    commit = api("/repos/%s/%s/git/commits/%s" % (OWNER, REPO, parent))
    tree = api("/repos/%s/%s/git/trees" % (OWNER, REPO),
               {"base_tree": commit["tree"]["sha"],
                "tree": [{"path": "inputs/kernel.macho", "mode": "100644",
                          "type": "blob", "sha": nb["sha"]}]})
    nc = api("/repos/%s/%s/git/commits" % (OWNER, REPO),
             {"message": "inputs: kernel built from %s\n" % sha, "tree": tree["sha"], "parents": [parent]})
    api("/repos/%s/%s/git/refs/heads/main" % (OWNER, REPO),
        {"sha": nc["sha"], "force": False}, method="PATCH")
    print("inputs committed", nc["sha"][:8], flush=True)

    run = find_run("build-meizu15-uefi-payload.yml", nc["sha"])
    if not run:
        sys.exit("no payload run")
    print("payload run", run["id"], flush=True)
    art = wait_artifact(run["id"], "payload-debug-full", "payload", 60)
    if not art:
        sys.exit("no payload artifact")

    OUT.mkdir(exist_ok=True)
    z = zipfile.ZipFile(io.BytesIO(fetch_artifact(art["id"])))
    for n in z.namelist():
        out = OUT / os.path.basename(n)
        out.write_bytes(z.read(n))
        print("wrote", out, out.stat().st_size, flush=True)
    print("\nnext: fastboot boot out/boot-meizu15-payload-debug.img  then photograph the panel")


if __name__ == "__main__":
    main()
