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
REVIEW_INSTRUCTIONS = {"AGENTS.md", "AGENTS.override.md", ".agents", ".codex"}


class CandidateConflict(RuntimeError):
    """Application changes need manual conflict resolution."""


def git(repository, *args, input=None, check=True):
    # Bare preparation never checks out incoming files. Ignore user/system Git
    # configuration so candidate attributes cannot select custom merge drivers.
    env = os.environ | {
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    }
    auth = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"] \
        if args[0] in ("fetch", "push") else []
    result = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *auth, "-C", str(repository), *args],
        input=input, encoding="utf-8", errors="surrogateescape", capture_output=True, env=env)
    if check and result.returncode:
        raise RuntimeError(f"Git {args[0]} failed: {result.stderr.strip()}")
    return result


def fetch_candidates(repository, base, upstream, current=None):
    git(repository, "init", "--bare")
    # Full ancestry is needed for three-way merges; shallow Actions checkouts
    # are deliberately not used as the preparation object store.
    refs = list(dict.fromkeys([base, *([current] if current else [])]))
    git(repository, "fetch", "--no-tags", f"https://github.com/{REPO}.git", *refs)
    git(repository, "fetch", "--no-tags", f"https://github.com/{UPSTREAM}.git", upstream)


def tree_entries(repository, tree):
    output = git(repository, "ls-tree", "-z", tree).stdout
    return {entry.split("\t", 1)[1]: entry for entry in output.split("\0") if entry}


def replace_entry(repository, tree, name, replacement):
    entries = tree_entries(repository, tree)
    entries[name] = f"040000 tree {replacement}\t{name}"
    return git(repository, "mktree", "-z", input="\0".join(entries.values()) + "\0").stdout.strip()


def freeze_workflows(repository, revision, base):
    """Restore the complete trusted workflow subtree, including its ancestors."""
    trusted_github = git(repository, "rev-parse", f"{base}:.github").stdout.strip()
    trusted = tree_entries(repository, trusted_github).get("workflows", "")
    if not trusted.startswith("040000 tree "):
        raise RuntimeError("Trusted main must contain a workflow directory")
    workflows = trusted.split("\t", 1)[0].split()[2]
    root = git(repository, "rev-parse", f"{revision}^{{tree}}").stdout.strip()
    incoming_github = tree_entries(repository, root).get(".github", "")
    # A file, symlink, or submodule cannot safely contain the trusted directory.
    # Restore trusted .github in that case; keep other incoming metadata when
    # .github is an ordinary directory.
    github = incoming_github.split("\t", 1)[0].split()[2] \
        if incoming_github.startswith("040000 tree ") else trusted_github
    github = replace_entry(repository, github, "workflows", workflows)
    return replace_entry(repository, root, ".github", github)


def freeze_instructions(repository, revision, base):
    """Keep reviewer instructions/configuration trusted at every directory scope."""
    root = git(repository, "rev-parse", f"{revision}^{{tree}}").stdout.strip()
    trusted_root = git(repository, "rev-parse", f"{base}^{{tree}}").stdout.strip()
    ancestors = set()
    for tree in (root, trusted_root):
        entries = git(repository, "ls-tree", "-r", "-t", "-z", tree).stdout
        for entry in filter(None, entries.split("\0")):
            parts = entry.split("\t", 1)[1].split("/")
            if parts[-1] in REVIEW_INSTRUCTIONS:
                ancestors.update("/".join(parts[:index]) for index in range(1, len(parts)))

    def restore(incoming, trusted, prefix=""):
        original = tree_entries(repository, incoming) if incoming else {}
        expected = tree_entries(repository, trusted) if trusted else {}
        entries = original.copy()
        for name in original.keys() | expected.keys():
            if name in REVIEW_INSTRUCTIONS:
                if name in expected:
                    entries[name] = expected[name]
                else:
                    entries.pop(name, None)
                continue
            path = f"{prefix}/{name}" if prefix else name
            if path not in ancestors:
                continue
            candidate_entry, trusted_entry = original.get(name, ""), expected.get(name, "")
            candidate_tree = candidate_entry.split("\t", 1)[0].split()[2] \
                if candidate_entry.startswith("040000 tree ") else None
            trusted_tree = trusted_entry.split("\t", 1)[0].split()[2] \
                if trusted_entry.startswith("040000 tree ") else None
            if candidate_tree:
                subtree = restore(candidate_tree, trusted_tree, path)
                entries[name] = f"040000 tree {subtree}\t{name}"
            elif trusted_tree:
                # A deleted, symlinked, or submodule ancestor cannot contain
                # trusted instructions. Restore its trusted directory instead.
                entries[name] = trusted_entry
        if entries == original and incoming:
            return incoming
        records = "\0".join(entries.values()) + ("\0" if entries else "")
        return git(repository, "mktree", "-z", input=records).stdout.strip()

    return restore(root, trusted_root)


def freeze_trusted(repository, revision, base):
    return freeze_instructions(repository, freeze_workflows(repository, revision, base), base)


def commit_tree(repository, tree, parents, message):
    parents = list(dict.fromkeys(parents))
    if len(parents) == 1 and tree == git(repository, "rev-parse", f"{parents[0]}^{{tree}}").stdout.strip():
        return parents[0]
    args = [argument for parent in parents for argument in ("-p", parent)]
    return git(repository, "commit-tree", tree, *args, input=message + "\n").stdout.strip()


def ancestor(repository, older, newer):
    result = git(repository, "merge-base", "--is-ancestor", older, newer, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Could not check candidate ancestry: {result.stderr.strip()}")
    return result.returncode == 0


def merge_tree(repository, left, right):
    result = git(repository, "merge-tree", "--write-tree", left, right, check=False)
    if result.returncode == 1:
        raise CandidateConflict(result.stdout.strip())
    if result.returncode:
        raise RuntimeError(f"Could not prepare upstream merge: {result.stderr.strip()}")
    return result.stdout.splitlines()[0]


def candidate_commit(repository, base, upstream, current=None):
    """Build off-ref; exclude untrusted workflows and reviewer instructions."""
    current = current or base
    safe_tree = freeze_trusted(repository, current, base)
    if (safe_tree == git(repository, "rev-parse", f"{current}^{{tree}}").stdout.strip()
            and ancestor(repository, base, current) and ancestor(repository, upstream, current)):
        return current
    safe_current = commit_tree(repository, safe_tree, [current],
                               "Preserve trusted workflows while preparing upstream sync")
    integrated = safe_current
    if not ancestor(repository, base, current):
        tree = merge_tree(repository, safe_current, base)
        integrated = commit_tree(repository, tree, [current, base], "Integrate latest fork main")
    if not ancestor(repository, upstream, integrated):
        safe_upstream = commit_tree(repository, freeze_trusted(repository, upstream, base), [upstream],
                                    "Exclude upstream workflows and reviewer instructions before integration")
        tree = merge_tree(repository, integrated, safe_upstream)
        # Temporary sanitized inputs stay off-ref. Published ancestry retains
        # the original commits rather than replacing upstream history.
        integrated = commit_tree(repository, tree, [integrated, upstream], "Sync upstream main")
    tree = freeze_trusted(repository, integrated, base)
    return commit_tree(repository, tree, [current, base, upstream], "Sync upstream main with trusted fork workflows")


def publish_candidate(repository, branch, head, base, expected=None):
    actual = git(repository, "rev-parse", f"{head}^{{tree}}").stdout.strip()
    if freeze_trusted(repository, head, base) != actual:
        raise RuntimeError("Refusing to publish untrusted candidate workflows or reviewer instructions")
    if expected and not ancestor(repository, expected, head):
        raise RuntimeError("Refusing to discard existing sync branch changes")
    refs = api(f"repos/{REPO}/git/matching-refs/heads/{branch}")
    existing = next((r["object"]["sha"] for r in refs if r["ref"] == f"refs/heads/{branch}"), None)
    if existing != expected:
        raise RuntimeError("Sync branch changed during preparation; retry on the next run")
    # A normal fast-forward push preserves concurrent descendant edits. The
    # helper reads GH_TOKEN from the environment; no credentials are persisted.
    if head != expected:
        git(repository, "push", f"https://github.com/{REPO}.git", f"{head}:refs/heads/{branch}")


def api(path, method="GET", data=None, token=None):
    args = ["gh", "api", path, "--method", method]
    if data is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=json.dumps(data) if data is not None else None,
                            encoding="utf-8", capture_output=True,
                            env=(os.environ | {"GH_TOKEN": token}) if token else None)
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
    decisions = {}
    for review in sorted(reviews, key=lambda r: r.get("id", 0)):
        if review["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            decisions[review["user"]["login"]] = review
    if any(r["state"] == "CHANGES_REQUESTED" for r in decisions.values()):
        return False
    current = [r for r in reviews if r["user"]["login"] == BOT
               and r["commit_id"] == sha and r["state"] != "DISMISSED"]
    latest = max(current, key=lambda r: r.get("id", 0)) if current else None
    if latest and latest["state"] != "APPROVED":
        return False
    if any(c["user"]["login"] == BOT and c["created_at"] >= request["created_at"]
           for c in comments):
        # Findings, errors, and quota notices all require attention.
        return False
    return any(r["user"]["login"] == BOT and r["content"] == "+1"
               for r in reactions) or any(
        r["user"]["login"] == BOT and r["commit_id"] == sha
        and r["state"] == "APPROVED" for r in reviews)


def recovery_pull(upstream, base=None):
    """Expose a conflict without resetting the normal integration branch."""
    root = f"repos/{REPO}"
    branch = "sync/upstream-conflict"
    pulls = [p for p in pages(f"{root}/pulls?state=open&base=main")
             if (p["head"].get("repo") or {}).get("full_name") == REPO
             and p["head"]["ref"].startswith(branch + "-")]
    if pulls:
        report(f"Resolve upstream conflicts in {pulls[0]['html_url']}; automation will not merge this PR.")
        return
    # A distinct immutable branch per upstream revision also preserves earlier fixes.
    branch += f"-{upstream}"
    pulls = api(f"{root}/pulls?state=open&base=main&head=gage006:{branch}")
    if pulls:
        report(f"Resolve upstream conflicts in {pulls[0]['html_url']}; automation will not merge this PR.")
        return
    refs = api(f"{root}/git/matching-refs/heads/{branch}")
    existing = next((r["object"]["sha"] for r in refs if r["ref"] == f"refs/heads/{branch}"), None)
    base = base or api(f"{root}/git/ref/heads/main")["object"]["sha"]
    with tempfile.TemporaryDirectory(prefix="upstream-recovery-") as directory:
        fetch_candidates(directory, base, upstream, existing)
        if existing:
            if freeze_trusted(directory, existing, base) != git(directory, "rev-parse", f"{existing}^{{tree}}").stdout.strip():
                raise RuntimeError("Existing recovery branch needs trusted workflows and reviewer instructions before opening a PR")
        else:
            head = commit_tree(directory, freeze_trusted(directory, upstream, base), [upstream],
                               "Expose upstream conflicts with trusted fork workflows and reviewer instructions")
            publish_candidate(directory, branch, head, base)
    pull = api(f"{root}/pulls", "POST", {
        "head": branch, "base": "main", "title": "Resolve upstream sync conflicts",
        "body": "Upstream could not merge cleanly into the fork. Merge main into this branch and resolve conflicts while preserving fork changes. Workflows and reviewer instructions remain those from trusted fork main; upstream changes to them require a separate manually reviewed PR. This recovery PR is never automatically merged. After resolving it, merge the recovery branch into sync/upstream for fresh CI and AI review, or test and merge this recovery PR manually. No device testing has been performed."})
    report(f"Upstream conflict recovery PR: {pull['html_url']}")


def branch_ci_ready(head):
    root = f"repos/{REPO}/actions/workflows/upstream-generate.yml"
    runs = api(f"{root}/runs?event=workflow_dispatch&branch=main&per_page=100")["workflow_runs"]
    runs = [r for r in runs if r["display_title"] == f"Upstream outputs {head}"]
    if not runs:
        api(f"{root}/dispatches", "POST", {"ref": "main", "inputs": {"head": head}},
            token=os.environ.get("GH_ACTIONS_TOKEN"))
        report("Dispatched isolated generation from trusted main before review.")
        return False
    if max(runs, key=lambda r: r["id"])["conclusion"] != "success":
        report("Waiting for successful branch CI; failed runs require attention.")
        return False
    return True


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
    # The guard and automation code were checked out at the queued workflow's
    # SHA. Never combine that older code with a newer API-selected main tree.
    if git(Path.cwd(), "rev-parse", "HEAD").stdout.strip() != base:
        report("Trusted main changed after checkout; waiting for the next sync run.")
        return
    if os.environ.get("SYNC_TOKEN_CONFIGURED") != "true":
        raise RuntimeError("Add UPSTREAM_SYNC_TOKEN before enabling unattended sync")
    if comparison["ahead_by"] == 0:
        return

    pulls = api(f"{root}/pulls?state=open&base=main&head=gage006:{BRANCH}")
    if len(pulls) > 1:
        raise RuntimeError("Multiple sync PRs found")
    refs = api(f"{root}/git/matching-refs/heads/{BRANCH}")
    current = next((r["object"]["sha"] for r in refs if r["ref"] == f"refs/heads/{BRANCH}"), None)
    with tempfile.TemporaryDirectory(prefix="upstream-candidate-") as directory:
        fetch_candidates(directory, base, upstream, current)
        try:
            head = candidate_commit(directory, base, upstream, current)
        except CandidateConflict:
            recovery_pull(upstream, base)
            raise
        publish_candidate(directory, BRANCH, head, base, current)
    if api(f"{root}/git/ref/heads/{BRANCH}")["object"]["sha"] != head:
        report("Sync branch changed after publication; waiting for the next run.")
        return
    if not pulls:
        body = """## Summary

Merge upstream main while preserving this fork's changes.
Workflows and reviewer instructions remain pinned to trusted fork main.
Upstream changes to them require a separate manually reviewed PR.

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
    if not branch_ci_ready(head):
        return
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
