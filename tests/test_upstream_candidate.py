"""Unreviewed upstream workflows must never reach a published fork branch."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "upstream_sync_candidate", Path(__file__).resolve().parents[1] / "scripts/upstream_sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = Path(directory.name)
        self.git("init", "-q", "--initial-branch=ancestor")
        self.git("config", "user.name", "Candidate Test")
        self.git("config", "user.email", "candidate-test@example.com")
        self.write("application.txt", "original application\n")
        self.write(".github/workflows/ci.yml", "name: Original CI\n")
        self.write(".github/workflows/retained.yaml", "name: Retained Workflow\n")
        self.write(".github/FUNDING.yml", "github: trusted-owner\n")
        self.ancestor = self.commit("Common ancestor")
        self.git("checkout", "-qb", "main")
        self.write(".github/workflows/ci.yml", "name: Trusted Fork CI\n")
        self.write("fork-only.txt", "fork behavior\n")
        self.base = self.commit("Trusted main")
        self.git("checkout", "-qb", "upstream", self.ancestor)

    def git(self, *arguments, check=True):
        result = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=check, text=True, capture_output=True)
        return result.stdout.strip()

    def write(self, name, content):
        path = self.repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def assert_safe_candidate(self, candidate, upstream, current=None):
        self.assertEqual(
            self.git("rev-parse", f"{candidate}:.github/workflows"),
            self.git("rev-parse", f"{self.base}:.github/workflows"))
        for ancestor in (self.base, upstream, current):
            if ancestor:
                self.git("merge-base", "--is-ancestor", ancestor, candidate)
        self.assertEqual(self.git("show", f"{candidate}:fork-only.txt"), "fork behavior")
        self.assertEqual(self.git("rev-parse", "upstream"), upstream)
        self.assertEqual(self.git("rev-parse", "main"), self.base)

    def prepare_remote(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.remote = Path(directory.name) / "fork.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        subprocess.run(
            ["git", "--git-dir", str(self.remote), "config", "core.logAllRefUpdates", "always"],
            check=True)
        self.git("remote", "add", "origin", str(self.remote))
        # Keep production HTTPS push paths local too; credentials never leave the fixture.
        self.git("config", f"url.{self.remote}.insteadOf", f"https://github.com/{sync.REPO}.git")
        self.git("push", "-q", "origin", f"{self.base}:refs/heads/main")

    def remote_git(self, *arguments, check=True):
        result = subprocess.run(
            ["git", "--git-dir", str(self.remote), *arguments],
            check=check, text=True, capture_output=True)
        return result.stdout.strip()

    def remote_api(self, path, method="GET", data=None, token=None):
        if method == "POST" and path == f"repos/{sync.REPO}/pulls":
            return {"html_url": "https://github.com/example/recovery"}
        self.assertEqual(method, "GET", f"Unexpected write API: {path}")
        if path == f"repos/{sync.UPSTREAM}/git/ref/heads/main":
            return {"object": {"sha": self.git("rev-parse", "upstream")}}
        if "/compare/" in path:
            return {"ahead_by": 1}
        marker = "/git/matching-refs/heads/"
        if marker in path:
            prefix = "refs/heads/" + path.split(marker, 1)[1]
            names = self.remote_git("for-each-ref", "--format=%(refname) %(objectname)", prefix)
            return [{"ref": name, "object": {"sha": sha}}
                    for name, sha in (line.split() for line in names.splitlines())]
        marker = "/git/ref/heads/"
        if marker in path:
            branch = path.split(marker, 1)[1]
            return {"object": {"sha": self.remote_git("rev-parse", f"refs/heads/{branch}")}}
        if "/pulls?" in path:
            return []
        self.fail(f"Unexpected API request: {path}")

    def local_network_git(self):
        original = sync.git

        def run(repository, *arguments, **kwargs):
            destination = self.remote if arguments[0] == "push" else self.repository
            arguments = tuple(str(destination) if isinstance(argument, str)
                              and argument.startswith("https://github.com/") else argument
                              for argument in arguments)
            return original(repository, *arguments, **kwargs)

        return patch.object(sync, "git", side_effect=run)

    def assert_published_workflows_are_trusted(self, branch):
        revisions = self.remote_git("reflog", "show", "--format=%H", f"refs/heads/{branch}").splitlines()
        self.assertTrue(revisions, "Expected at least one published candidate")
        trusted = self.git("rev-parse", f"{self.base}:.github/workflows")
        for revision in revisions:
            self.assertEqual(self.remote_git("rev-parse", f"{revision}:.github/workflows"), trusted)

    def test_workflow_changes_and_workflow_only_conflicts_keep_trusted_tree(self):
        self.write(".github/workflows/ci.yml", "name: Unreviewed CI\npermissions: write-all\n")
        self.write(".github/workflows/steal-secrets.yaml", "name: Unreviewed Secret Access\n")
        (self.repository / ".github/workflows/retained.yaml").unlink()
        self.write("upstream-only.txt", "upstream application addition\n")
        upstream = self.commit("Workflow additions, modifications, deletions, and code")

        candidate = sync.candidate_commit(self.repository, self.base, upstream)

        self.assert_safe_candidate(candidate, upstream)
        self.assertEqual(self.git("show", f"{candidate}:upstream-only.txt"), "upstream application addition")
        names = self.git("ls-tree", "-r", "--name-only", candidate).splitlines()
        self.assertNotIn(".github/workflows/steal-secrets.yaml", names)
        self.assertIn(".github/workflows/retained.yaml", names)

    def test_workflow_file_replaced_with_directory_is_removed(self):
        workflow = self.repository / ".github/workflows/ci.yml"
        workflow.unlink()
        self.write(".github/workflows/ci.yml/extra.yaml", "name: Extra Unreviewed Workflow\n")
        upstream = self.commit("Replace workflow file with a tree")

        candidate = sync.candidate_commit(self.repository, self.base, upstream)

        self.assert_safe_candidate(candidate, upstream)

    def test_workflow_directory_replaced_with_symlink_is_restored(self):
        shutil.rmtree(self.repository / ".github/workflows")
        self.write("rogue-workflows/ci.yml", "name: Symlink Workflow\n")
        (self.repository / ".github/workflows").symlink_to("../rogue-workflows", target_is_directory=True)
        upstream = self.commit("Replace workflow tree with a symlink")

        candidate = sync.candidate_commit(self.repository, self.base, upstream)

        self.assert_safe_candidate(candidate, upstream)

    def test_workflow_ancestor_replaced_with_symlink_is_restored(self):
        shutil.rmtree(self.repository / ".github")
        self.write("rogue-github/workflows/ci.yml", "name: Ancestor Symlink Workflow\n")
        (self.repository / ".github").symlink_to("rogue-github", target_is_directory=True)
        upstream = self.commit("Replace github ancestor with a symlink")

        candidate = sync.candidate_commit(self.repository, self.base, upstream)

        self.assert_safe_candidate(candidate, upstream)

    def test_workflow_tree_deletion_is_restored(self):
        shutil.rmtree(self.repository / ".github/workflows")
        upstream = self.commit("Delete upstream workflow tree")

        candidate = sync.candidate_commit(self.repository, self.base, upstream)

        self.assert_safe_candidate(candidate, upstream)

    def test_existing_candidate_fixes_survive_while_its_workflows_are_replaced(self):
        self.write("upstream-only.txt", "incoming application addition\n")
        upstream = self.commit("Incoming upstream code")
        self.git("checkout", "-qb", "existing-candidate", self.base)
        self.write("candidate-fix.txt", "manual integration fix\n")
        self.write(".github/workflows/unreviewed.yml", "name: Existing Unsafe Workflow\n")
        current = self.commit("Manual candidate fix and unsafe workflow")

        candidate = sync.candidate_commit(self.repository, self.base, upstream, current)

        self.assert_safe_candidate(candidate, upstream, current)
        self.assertEqual(self.git("show", f"{candidate}:candidate-fix.txt"), "manual integration fix")
        self.assertEqual(self.git("show", f"{candidate}:upstream-only.txt"), "incoming application addition")

    def test_repeated_poll_keeps_the_same_candidate_revision(self):
        self.write("upstream-only.txt", "incoming application addition\n")
        upstream = self.commit("Incoming upstream code")
        first = sync.candidate_commit(self.repository, self.base, upstream)

        second = sync.candidate_commit(self.repository, self.base, upstream, first)

        self.assertEqual(second, first, "Unchanged polling must preserve CI and review evidence")
        self.assert_safe_candidate(second, upstream, first)
        self.prepare_remote()
        with patch.object(sync, "api", side_effect=self.remote_api), patch.dict(os.environ, {"GH_TOKEN": "fixture-token"}):
            sync.publish_candidate(self.repository, sync.BRANCH, first, self.base)
            sync.publish_candidate(self.repository, sync.BRANCH, second, self.base, expected=first)
        published = self.remote_git("reflog", "show", "--format=%H", f"refs/heads/{sync.BRANCH}").splitlines()
        self.assertEqual(published, [first])

    def test_existing_candidate_includes_new_main_without_losing_its_history(self):
        self.write("upstream-only.txt", "incoming application addition\n")
        upstream = self.commit("Incoming upstream code")
        first = sync.candidate_commit(self.repository, self.base, upstream)
        self.git("checkout", "main")
        self.write("main-fix.txt", "new stable-branch behavior\n")
        self.base = self.commit("Main advances during review")

        candidate = sync.candidate_commit(self.repository, self.base, upstream, first)

        self.assertNotEqual(candidate, first)
        self.assert_safe_candidate(candidate, upstream, first)
        self.assertEqual(self.git("show", f"{candidate}:main-fix.txt"), "new stable-branch behavior")
        self.assertEqual(self.git("show", f"{candidate}:upstream-only.txt"), "incoming application addition")

    def test_application_conflicts_raise_before_any_api_or_ref_publication(self):
        self.write("application.txt", "upstream conflicting behavior\n")
        self.write(".github/workflows/new.yml", "name: Unreviewed Workflow\n")
        upstream = self.commit("Incoming conflicting application")
        self.git("checkout", "main")
        self.write("application.txt", "fork conflicting behavior\n")
        self.base = self.commit("Fork conflicting application")
        refs_before = self.git("show-ref")

        with patch.object(sync, "api") as api:
            with self.assertRaises(sync.CandidateConflict):
                sync.candidate_commit(self.repository, self.base, upstream)

        api.assert_not_called()
        self.assertEqual(self.git("show-ref"), refs_before)

    def test_initial_branch_publication_contains_only_sanitized_workflows(self):
        self.write(".github/workflows/ci.yml", "name: Unreviewed CI\n")
        self.write(".github/workflows/unsafe.yaml", "name: Unreviewed Workflow\n")
        upstream = self.commit("Unsafe upstream workflows")
        candidate = sync.candidate_commit(self.repository, self.base, upstream)
        self.prepare_remote()

        with patch.object(sync, "api", side_effect=self.remote_api), patch.dict(os.environ, {"GH_TOKEN": "fixture-token"}):
            sync.publish_candidate(self.repository, sync.BRANCH, candidate, self.base)

        self.assertEqual(self.remote_git("rev-parse", f"refs/heads/{sync.BRANCH}"), candidate)
        self.assert_published_workflows_are_trusted(sync.BRANCH)

    def test_unsanitized_candidate_cannot_be_published(self):
        self.write(".github/workflows/unsafe.yaml", "name: Unreviewed Workflow\n")
        upstream = self.commit("Unsafe upstream workflows")
        self.prepare_remote()
        refs_before = self.remote_git("show-ref")

        with patch.object(sync, "api", side_effect=self.remote_api), patch.dict(os.environ, {"GH_TOKEN": "fixture-token"}):
            with self.assertRaises(RuntimeError):
                sync.publish_candidate(self.repository, sync.BRANCH, upstream, self.base)

        self.assertEqual(self.remote_git("show-ref"), refs_before)

    def test_changed_existing_branch_head_cannot_be_overwritten(self):
        self.write("upstream-only.txt", "incoming application addition\n")
        upstream = self.commit("Incoming upstream code")
        candidate = sync.candidate_commit(self.repository, self.base, upstream)
        self.prepare_remote()
        self.git("push", "-q", "origin", f"{self.base}:refs/heads/{sync.BRANCH}")
        self.git("checkout", "main")
        self.write("concurrent-fix.txt", "preserve concurrent manual change\n")
        concurrent = self.commit("Concurrent candidate change")
        self.git("push", "-q", "origin", f"{concurrent}:refs/heads/{sync.BRANCH}")
        refs_before = self.remote_git("show-ref")

        with patch.object(sync, "api", side_effect=self.remote_api), patch.dict(os.environ, {"GH_TOKEN": "fixture-token"}):
            with self.assertRaises(RuntimeError):
                sync.publish_candidate(self.repository, sync.BRANCH, candidate, self.base, expected=self.base)

        self.assertEqual(self.remote_git("show-ref"), refs_before)

    def test_branch_created_since_snapshot_cannot_be_overwritten(self):
        self.write("upstream-only.txt", "incoming application addition\n")
        upstream = self.commit("Incoming upstream code")
        candidate = sync.candidate_commit(self.repository, self.base, upstream)
        self.prepare_remote()
        self.git("push", "-q", "origin", f"{self.base}:refs/heads/{sync.BRANCH}")
        refs_before = self.remote_git("show-ref")

        with patch.object(sync, "api", side_effect=self.remote_api), patch.dict(os.environ, {"GH_TOKEN": "fixture-token"}):
            with self.assertRaises(RuntimeError):
                sync.publish_candidate(self.repository, sync.BRANCH, candidate, self.base)

        self.assertEqual(self.remote_git("show-ref"), refs_before)

    def test_concurrent_edit_after_ref_check_is_preserved_by_non_force_push(self):
        self.write("upstream-only.txt", "incoming application addition\n")
        upstream = self.commit("Incoming upstream code")
        candidate = sync.candidate_commit(self.repository, self.base, upstream)
        self.prepare_remote()
        self.git("push", "-q", "origin", f"{self.base}:refs/heads/{sync.BRANCH}")
        self.git("checkout", "main")
        self.write("concurrent-fix.txt", "preserve concurrent manual change\n")
        concurrent = self.commit("Concurrent candidate edit")

        def advance_after_snapshot(path, *arguments, **kwargs):
            response = self.remote_api(path, *arguments, **kwargs)
            self.git("push", "-q", "origin", f"{concurrent}:refs/heads/{sync.BRANCH}")
            return response

        with patch.object(sync, "api", side_effect=advance_after_snapshot), \
                patch.dict(os.environ, {"GH_TOKEN": "fixture-token"}):
            with self.assertRaisesRegex(RuntimeError, "Git push failed"):
                sync.publish_candidate(self.repository, sync.BRANCH, candidate, self.base, expected=self.base)

        self.assertEqual(self.remote_git("rev-parse", f"refs/heads/{sync.BRANCH}"), concurrent)
        published = self.remote_git("reflog", "show", "--format=%H", f"refs/heads/{sync.BRANCH}").splitlines()
        self.assertEqual(published, [concurrent, self.base])

    def test_application_conflict_publishes_only_sanitized_recovery_branch(self):
        self.write("application.txt", "upstream conflicting behavior\n")
        self.write(".github/workflows/unsafe.yaml", "name: Unreviewed Workflow\n")
        upstream = self.commit("Unsafe workflows and conflicting application")
        self.git("checkout", "main")
        self.write("application.txt", "fork conflicting behavior\n")
        self.base = self.commit("Fork conflicting application")
        self.prepare_remote()
        environment = {"GH_TOKEN": "fixture-token", "DRY_RUN": "false", "SYNC_TOKEN_CONFIGURED": "true"}

        with patch.object(sync, "api", side_effect=self.remote_api), self.local_network_git(), patch.dict(os.environ, environment):
            with self.assertRaises(sync.CandidateConflict):
                sync.main()

        branch = f"sync/upstream-conflict-{upstream}"
        self.assert_published_workflows_are_trusted(branch)
        head = self.remote_git("rev-parse", f"refs/heads/{branch}")
        self.assertNotEqual(head, upstream)
        self.remote_git("merge-base", "--is-ancestor", upstream, head)
        self.assertEqual(self.remote_git("rev-parse", "refs/heads/main"), self.base)
        self.assertEqual(self.remote_git("show-ref", "--verify", f"refs/heads/{sync.BRANCH}", check=False), "")

    def test_unsafe_existing_recovery_ref_cannot_open_a_new_pr(self):
        self.write(".github/workflows/unsafe.yaml", "name: Unreviewed Workflow\n")
        upstream = self.commit("Unsafe upstream workflows")
        self.prepare_remote()
        branch = f"sync/upstream-conflict-{upstream}"
        self.git("push", "-q", "origin", f"{upstream}:refs/heads/{branch}")
        refs_before = self.remote_git("show-ref")

        with patch.object(sync, "api", side_effect=self.remote_api) as api, self.local_network_git(), \
                patch.dict(os.environ, {"GH_TOKEN": "fixture-token"}):
            with self.assertRaisesRegex(RuntimeError, "trusted workflows"):
                sync.recovery_pull(upstream, self.base)

        self.assertEqual(self.remote_git("show-ref"), refs_before)
        self.assertFalse(any(call.args[1:2] == ("POST",) for call in api.call_args_list))


if __name__ == "__main__":
    unittest.main()
