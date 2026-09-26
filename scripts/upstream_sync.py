#!/usr/bin/env python3
"""Maintain one upstream PR; merge only after fresh CI and Codex review."""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = "gage006/espcontrol"
UPSTREAM = "jtenniswood/espcontrol"
BRANCH = "sync/upstream"
BOT = "chatgpt-codex-connector[bot]"


def api(path, method="GET", data=None):
    args = ["gh", "api", path, "--method", method]
    if data is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=json.dumps(data) if data is not None else None,
                            encoding="utf-8", capture_output=True)
    if result.returncode:
        raise RuntimeError(f"GitHub API {method} {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def pages(path):
    result = []
    separator = "&" if "?" in path else "?"
    for page in range(1, 101):
        batch = api(f"{path}{separator}per_page=100&page={page}")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError("Pagination limit reached; refusing an incomplete decision")


def report(message):
    print(message)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(message + "\n\n")


def review_passed(comments, reviews, request, reactions, sha):
    # No generic 'looks good' text parsing: only the Codex identity may clear review.
    if any(r["state"] == "CHANGES_REQUESTED" for r in reviews):
        return False
    if any(r["user"]["login"] == BOT and r["commit_id"] == sha
           and r["state"] != "APPROVED" for r in reviews):
        return False
    if any(c["user"]["login"] == BOT and c["created_at"] >= request["created_at"]
           for c in comments):
        # Findings, errors, and quota notices all require attention.
        return False
    return any(r["user"]["login"] == BOT and r["content"] == "+1"
               for r in reactions) or any(
        r["user"]["login"] == BOT and r["commit_id"] == sha
        and r["state"] == "APPROVED" for r in reviews)


def check_fork_candidate(head):
    # Use the trusted main checkout's guard, never a script from the candidate.
    subprocess.run(["git", "fetch", "origin", head], check=True)
    with tempfile.TemporaryDirectory(prefix="fork-candidate-") as directory:
        archive = Path(directory) / "candidate.tar"
        subprocess.run(["git", "archive", head, "-o", str(archive)], check=True)
        candidate = Path(directory) / "tree"
        candidate.mkdir()
        with tarfile.open(archive) as bundle:
            bundle.extractall(candidate, filter="data")
        subprocess.run([sys.executable, "scripts/check_fork_config.py", str(candidate)], check=True)


def main():
    root = f"repos/{REPO}"
    base = api(f"{root}/git/ref/heads/main")["object"]["sha"]
    upstream = api(f"repos/{UPSTREAM}/git/ref/heads/main")["object"]["sha"]
    comparison = api(f"{root}/compare/{base}...{upstream}")
    report(f"Upstream commits missing from main: {comparison['ahead_by']}")
    if os.environ.get("DRY_RUN", "true").lower() == "true":
        report("Dry run: no branches, PRs, comments, or merges changed.")
        return
    if os.environ.get("SYNC_TOKEN_CONFIGURED") != "true":
        raise RuntimeError("Add UPSTREAM_SYNC_TOKEN before enabling unattended sync")
    if comparison["ahead_by"] == 0:
        return

    pulls = api(f"{root}/pulls?state=open&base=main&head=gage006:{BRANCH}")
    if len(pulls) > 1:
        raise RuntimeError("Multiple sync PRs found")
    refs = api(f"{root}/git/matching-refs/heads/{BRANCH}")
    if not any(r["ref"] == f"refs/heads/{BRANCH}" for r in refs):
        api(f"{root}/git/refs", "POST", {"ref": f"refs/heads/{BRANCH}", "sha": base})
    # GitHub creates merge commits without executing any incoming code.
    # A conflict produces an API error and leaves main untouched.
    for sha in (base, upstream):
        api(f"{root}/merges", "POST", {"base": BRANCH, "head": sha})
    head = api(f"{root}/git/ref/heads/{BRANCH}")["object"]["sha"]
    if not pulls:
        body = """## Summary

Merge upstream main while preserving this fork's changes.

## Documentation decision

- [x] No docs needed because this does not change user-visible behavior/configuration.

Docs notes: This PR imports upstream commits, including their documentation. Review their changes together.

## Testing

- [ ] Automated/local checks passed or were run where practical.
- [x] Device testing is not required; explain why in Notes for testing.

## Notes for testing

The owner authorized unattended upstream sync after Codex review and successful CI.
CI is automated evidence only; no display has been flashed or physically tested.
See the generated PR Testing Guidance for affected devices and manual test steps.

## PR status

- [x] Checks running or waiting.

## Issue handling

- [x] Do not close related issues until the user confirms the fix works.
"""
        pull = api(f"{root}/pulls", "POST", {"head": BRANCH, "base": "main",
                   "title": "Sync upstream main", "body": body})
    else:
        pull = pulls[0]
    number = pull["number"]
    report(f"Sync PR: {pull['html_url']}")
    comments = pages(f"{root}/issues/{number}/comments")
    actor = api("user")["login"]
    marker = f"<!-- upstream-sync-review:{head} -->"
    request = next((c for c in comments if c["body"].startswith(marker)
                    and c["user"]["login"] == actor), None)
    if request is None:
        api(f"{root}/issues/{number}/comments", "POST", {
            "body": f"{marker}\n@codex review\n\nReview commit {head}, including compatibility with this fork."})
        report("Requested Codex review; a later scheduled run will check the result.")
        return
    reactions = pages(f"{root}/issues/comments/{request['id']}/reactions")
    reviews = pages(f"{root}/pulls/{number}/reviews")
    if not review_passed(comments, reviews, request, reactions, head):
        report("Waiting for a clean Codex review of the current commit.")
        return
    # Strict branch protection is essential: GitHub must enforce freshness even
    # if main advances between this process's final read and the merge request.
    protection = api(f"{root}/branches/main/protection")
    required = protection.get("required_status_checks") or {}
    contexts = set(required.get("contexts", []))
    contexts.update(c["context"] for c in required.get("checks", []))
    if (not required.get("strict") or "CI Gate" not in contexts
            or not protection.get("enforce_admins", {}).get("enabled")
            or not protection.get("required_conversation_resolution", {}).get("enabled")):
        raise RuntimeError("main must require CI Gate, up-to-date branches, resolved conversations, and enforce rules for administrators")
    runs = api(f"{root}/actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}&per_page=100")["workflow_runs"]
    runs = [r for r in runs if any(p["number"] == number for p in r["pull_requests"])]
    if not runs or max(runs, key=lambda r: r["id"])["conclusion"] != "success":
        report("Waiting for successful CI on the current sync PR commit.")
        return
    check_fork_candidate(head)
    fresh = api(f"{root}/pulls/{number}")
    if fresh["head"]["sha"] != head or fresh["draft"] or fresh["mergeable_state"] != "clean":
        report("PR changed or is not mergeable; leaving it open.")
        return
    merged = api(f"{root}/pulls/{number}/merge", "PUT", {"sha": head, "merge_method": "merge"})
    if not merged["merged"]:
        raise RuntimeError(merged["message"])
    report(f"Merged upstream sync PR #{number} after Codex review and CI.")


if __name__ == "__main__":
    main()
