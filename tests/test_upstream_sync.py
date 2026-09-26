"""Review decisions must fail closed for missing, stale, or adverse evidence."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "upstream_sync", Path(__file__).resolve().parents[1] / "scripts/upstream_sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class ReviewDecisionTests(unittest.TestCase):
    def setUp(self):
        self.request = {"created_at": "2026-09-26T10:00:00Z"}
        self.thumb = {"user": {"login": sync.BOT}, "content": "+1"}

    def decide(self, comments=None, reviews=None, reactions=None):
        return sync.review_passed(comments or [], reviews or [], self.request,
                                  reactions or [], "current")

    def test_no_review_does_not_merge(self):
        self.assertFalse(self.decide())

    def test_codex_thumb_on_commit_specific_request_passes(self):
        self.assertTrue(self.decide(reactions=[self.thumb]))

    def test_other_users_cannot_clear_review(self):
        self.assertFalse(self.decide(reactions=[{
            "user": {"login": "someone-else"}, "content": "+1"}]))

    def test_eyes_does_not_mean_success(self):
        self.assertFalse(self.decide(reactions=[{
            "user": {"login": sync.BOT}, "content": "eyes"}]))

    def test_error_or_findings_override_thumb(self):
        self.assertFalse(self.decide(comments=[{
            "user": {"login": sync.BOT}, "created_at": "2026-09-26T10:01:00Z",
            "body": "Review quota exceeded"}], reactions=[self.thumb]))

    def test_codex_review_findings_override_thumb(self):
        self.assertFalse(self.decide(reviews=[{
            "user": {"login": sync.BOT}, "commit_id": "current", "state": "COMMENTED"}],
            reactions=[self.thumb]))

    def test_stale_approval_does_not_pass(self):
        self.assertFalse(self.decide(reviews=[{
            "user": {"login": sync.BOT}, "commit_id": "old", "state": "APPROVED"}]))

    def test_human_change_request_blocks_merge(self):
        self.assertFalse(self.decide(reviews=[{
            "user": {"login": "maintainer"}, "commit_id": "current", "state": "CHANGES_REQUESTED"}],
            reactions=[self.thumb]))

    def test_later_approval_supersedes_change_request(self):
        reviews = [
            {"id": 1, "user": {"login": "maintainer"}, "commit_id": "old", "state": "CHANGES_REQUESTED"},
            {"id": 2, "user": {"login": "maintainer"}, "commit_id": "current", "state": "APPROVED"},
        ]
        self.assertTrue(self.decide(reviews=reviews, reactions=[self.thumb]))

    def test_comment_does_not_dismiss_change_request(self):
        reviews = [
            {"id": 1, "user": {"login": "maintainer"}, "commit_id": "old", "state": "CHANGES_REQUESTED"},
            {"id": 2, "user": {"login": "maintainer"}, "commit_id": "current", "state": "COMMENTED"},
        ]
        self.assertFalse(self.decide(reviews=reviews, reactions=[self.thumb]))

    def test_dismissed_change_request_does_not_block(self):
        self.assertTrue(self.decide(reviews=[{
            "user": {"login": "maintainer"}, "commit_id": "old", "state": "DISMISSED"}],
            reactions=[self.thumb]))

    def test_first_conflict_creates_separate_reviewable_branch(self):
        from unittest.mock import patch
        with patch.object(sync, "pages", return_value=[]), patch.object(sync, "api") as calls:
            calls.side_effect = [[], [], None, {"html_url": "https://github.com/example/recovery"}]
            sync.recovery_pull("upstream-sha")
            self.assertEqual(calls.call_args_list[2].args[2], {
                "ref": "refs/heads/sync/upstream-conflict-upstream-sha", "sha": "upstream-sha"})
            self.assertEqual(calls.call_args.args[2]["head"], "sync/upstream-conflict-upstream-sha")

    def test_existing_recovery_pr_is_preserved(self):
        from unittest.mock import patch
        pull = {"head": {"repo": {"full_name": sync.REPO}, "ref": "sync/upstream-conflict-old"},
                "html_url": "https://github.com/example/recovery"}
        with patch.object(sync, "pages", return_value=[pull]), patch.object(sync, "api") as calls:
            sync.recovery_pull("new")
            calls.assert_not_called()

    def test_branch_ci_dispatch_uses_builtin_token(self):
        from unittest.mock import patch
        with patch.object(sync, "api") as calls, patch.dict(sync.os.environ, {"GH_ACTIONS_TOKEN": "test"}):
            calls.side_effect = [{"workflow_runs": []}, None]
            self.assertFalse(sync.branch_ci_ready("current"))
            self.assertTrue(calls.call_args.args[0].endswith("/dispatches"))
            self.assertEqual(calls.call_args.kwargs, {"token": "test"})

    def test_branch_ci_does_not_repeat_pending_or_failed_runs(self):
        from unittest.mock import patch
        for conclusion in (None, "failure", "cancelled", "success"):
            with patch.object(sync, "api", return_value={"workflow_runs": [
                    {"id": 1, "conclusion": conclusion}]}) as calls:
                self.assertEqual(sync.branch_ci_ready("current"), conclusion == "success")
                calls.assert_called_once()

    def test_dry_run_never_writes(self):
        from unittest.mock import patch
        with patch.object(sync, "api") as mock_api, patch.dict(
                sync.os.environ, {"DRY_RUN": "true"}):
            mock_api.side_effect = [{"object": {"sha": "base"}},
                                    {"object": {"sha": "upstream"}}, {"ahead_by": 4}]
            sync.main()
            self.assertEqual(mock_api.call_count, 3)
            self.assertTrue(all(len(c.args) == 1 for c in mock_api.call_args_list))

    def run_sync(self, conclusion="success", strict=True, fresh_head="current"):
        from unittest.mock import patch
        root = f"repos/{sync.REPO}"
        pull = {"number": 8, "html_url": "https://github.com/example/pull/8"}
        request = {"id": 12, "body": "<!-- upstream-sync-review:current -->",
                   "user": {"login": "owner"}, **self.request}
        responses = {
            f"{root}/git/ref/heads/main": {"object": {"sha": "base"}},
            f"repos/{sync.UPSTREAM}/git/ref/heads/main": {"object": {"sha": "upstream"}},
            f"{root}/compare/base...upstream": {"ahead_by": 4},
            f"{root}/pulls?state=open&base=main&head=gage006:{sync.BRANCH}": [pull],
            f"{root}/git/matching-refs/heads/{sync.BRANCH}": [{"ref": f"refs/heads/{sync.BRANCH}"}],
            f"{root}/merges": None,
            f"{root}/git/ref/heads/{sync.BRANCH}": {"object": {"sha": "current"}},
            "user": {"login": "owner"},
            f"{root}/branches/main/protection": {
                "required_status_checks": {"strict": strict, "contexts": ["CI Gate"]},
                "enforce_admins": {"enabled": True},
                "required_conversation_resolution": {"enabled": True}},
            f"{root}/actions/workflows/ci.yml/runs?event=pull_request&head_sha=current&per_page=100": {
                "workflow_runs": [{"id": 1, "pull_requests": [{"number": 8}], "conclusion": conclusion}]},
            f"{root}/pulls/8": {"head": {"sha": fresh_head}, "draft": False, "mergeable_state": "clean"},
            f"{root}/pulls/8/merge": {"merged": True},
        }
        lists = {f"{root}/issues/8/comments": [request],
                 f"{root}/issues/comments/12/reactions": [self.thumb],
                 f"{root}/pulls/8/reviews": []}
        with patch.object(sync, "api", side_effect=lambda path, *args: responses[path]) as calls, \
                patch.object(sync, "pages", side_effect=lambda path: lists[path]), \
                patch.object(sync, "check_fork_candidate"), \
                patch.object(sync, "branch_ci_ready", return_value=True), \
                patch.dict(sync.os.environ, {"DRY_RUN": "false", "SYNC_TOKEN_CONFIGURED": "true"}):
            sync.main()
        return calls

    def test_merge_pins_reviewed_head_and_preserves_history(self):
        calls = self.run_sync()
        self.assertEqual(calls.call_args.args, (
            f"repos/{sync.REPO}/pulls/8/merge", "PUT", {"sha": "current", "merge_method": "merge"}))

    def test_pending_or_failed_ci_does_not_merge(self):
        for conclusion in (None, "failure", "cancelled", "skipped"):
            with self.subTest(conclusion=conclusion):
                calls = self.run_sync(conclusion=conclusion)
                self.assertFalse(any(c.args[0].endswith("/merge") for c in calls.call_args_list))

    def test_changed_head_does_not_merge(self):
        calls = self.run_sync(fresh_head="changed")
        self.assertFalse(any(c.args[0].endswith("/merge") for c in calls.call_args_list))

    def test_missing_strict_protection_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "main must require"):
            self.run_sync(strict=False)


if __name__ == "__main__":
    unittest.main()
