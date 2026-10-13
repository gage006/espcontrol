#!/usr/bin/env python3
"""Pass generated data across a read-only generation / trusted publishing boundary."""

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from upstream_sync import api, BRANCH, REPO

ROOT = f"repos/{REPO}"
PATHS = ["common/assets/icon_glyphs.yaml", "components/espcontrol/icons.h",
         "src/webserver/generated/icons.ts", "docs/public/webserver"]


def allowed(path):
    if not isinstance(path, str) or any(p in ("", ".", "..") for p in path.split("/")):
        return False
    return path in PATHS[:3] or bool(re.fullmatch(
        r"docs/public/webserver/(?:web-assets\.json|(?:[a-z0-9-]+/)?www\.js|bundles/[a-zA-Z0-9_./-]+\.(?:js|json))", path))


def verify():
    head = os.environ["HEAD_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("Expected a full commit SHA")
    if api(f"{ROOT}/git/ref/heads/{BRANCH}")["object"]["sha"] != head:
        raise RuntimeError("Sync branch changed; wait for the next sync run")
    return head


def export(target):
    subprocess.run(["git", "add", "-A", "--", *PATHS], check=True)
    names = subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z", "HEAD"], encoding="utf-8")
    entries = {}
    for name in filter(None, names.split("\0")):
        if not allowed(name):
            raise ValueError(f"Unexpected generated file: {name}")
        path = Path(name)
        if path.is_symlink():
            raise ValueError("Generated symlinks are not allowed")
        entries[name] = base64.b64encode(path.read_bytes()).decode("ascii") if path.exists() else None
    Path(target).write_text(json.dumps(entries), encoding="utf-8")


def validate(entries):
    if not isinstance(entries, dict) or len(entries) > 1000:
        raise ValueError("Invalid generated file map")
    size = 0
    for path, content in entries.items():
        if not allowed(path):
            raise ValueError(f"Unexpected generated file: {path}")
        if content is not None:
            if not isinstance(content, str):
                raise ValueError("Expected base64 file contents")
            size += len(base64.b64decode(content, validate=True))
    if size > 20 * 1024 * 1024:
        raise ValueError("Generated output exceeds 20 MiB")


def publish(target):
    head = verify()
    path = Path(target)
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("Generated artifact is too large")
    entries = json.loads(path.read_text(encoding="utf-8"))
    validate(entries)  # Validate all paths before any write API call.
    if not entries:
        print("Generated files are already current")
        return
    original = api(f"{ROOT}/git/commits/{head}")
    tree = []
    for name, content in entries.items():
        blob = api(f"{ROOT}/git/blobs", "POST", {"content": content, "encoding": "base64"}) if content is not None else None
        tree.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"] if blob else None})
    result = api(f"{ROOT}/git/trees", "POST", {"base_tree": original["tree"]["sha"], "tree": tree})
    if result["sha"] == original["tree"]["sha"]:
        return
    commit = api(f"{ROOT}/git/commits", "POST", {
        "message": "Regenerate upstream icon and web outputs", "tree": result["sha"], "parents": [head]})
    verify()
    # Non-force update rejects concurrent changes; published data is reviewed by
    # fresh PR CI and AI review before main can receive it.
    api(f"{ROOT}/git/refs/heads/{BRANCH}", "PATCH", {"sha": commit["sha"], "force": False})


if __name__ == "__main__":
    if sys.argv[1] == "verify":
        verify()
    elif sys.argv[1] == "export":
        export(sys.argv[2])
    elif sys.argv[1] == "publish":
        publish(sys.argv[2])
    else:
        raise ValueError("Unknown command")
