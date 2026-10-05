"""The publishing job treats generation artifacts as untrusted data."""

import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import upstream_generated as generated


class GeneratedDataTests(unittest.TestCase):
    def test_allowlist_rejects_executable_control_files_and_traversal(self):
        for name in ("scripts/upstream_sync.py", ".github/workflows/ci.yml",
                     "docs/public/webserver/../../scripts/evil.js",
                     "docs/public/webserver/bundles/../evil.js",
                     "docs/public/webserver/bundles//evil.js",
                     "/docs/public/webserver/www.js", "docs/public/webserver/www.js/evil",
                     "docs/public/webserver/bundles/evil.py", "docs\\public\\webserver\\www.js"):
            with self.subTest(name=name):
                self.assertFalse(generated.allowed(name))

    def test_generated_paths_are_accepted(self):
        for name in ("components/espcontrol/icons.h", "common/assets/icon_glyphs.yaml",
                     "src/webserver/entry.ts", "docs/public/webserver/www.js",
                     "docs/public/webserver/embedded/www.js", "docs/public/webserver/web-assets.json",
                     "docs/public/webserver/bundles/embedded/chunk-abc.js"):
            self.assertTrue(generated.allowed(name), name)

    def test_invalid_payload_rejected_before_any_api_write(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "data.json"
            artifact.write_text(json.dumps({"scripts/evil.py": "YWJj"}))
            with patch.object(generated, "verify", return_value="a" * 40), patch.object(generated, "api") as api:
                with self.assertRaises(ValueError):
                    generated.publish(artifact)
                api.assert_not_called()

    def test_invalid_base64_and_non_map_rejected(self):
        for value in ([], {"src/webserver/entry.ts": "!not-base64!"},
                      {"src/webserver/entry.ts": 42}):
            with self.assertRaises(ValueError):
                generated.validate(value)

    def test_empty_artifact_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "data.json"
            artifact.write_text("{}")
            with patch.object(generated, "verify", return_value="a" * 40), patch.object(generated, "api") as api:
                generated.publish(artifact)
                api.assert_not_called()

    def test_stale_or_invalid_head_rejected(self):
        with patch.dict(os.environ, {"HEAD_SHA": "not-a-sha"}), patch.object(generated, "api") as api:
            with self.assertRaises(ValueError):
                generated.verify()
            api.assert_not_called()
        with patch.dict(os.environ, {"HEAD_SHA": "a" * 40}), patch.object(
                generated, "api", return_value={"object": {"sha": "b" * 40}}):
            with self.assertRaises(RuntimeError):
                generated.verify()

    def test_publisher_writes_data_only_and_never_force_pushes(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "data.json"
            artifact.write_text(json.dumps({"src/webserver/entry.ts": base64.b64encode(b"data").decode()}))
            with patch.object(generated, "verify", return_value="a" * 40) as verify, \
                    patch.object(generated, "api") as api, patch.object(generated.subprocess, "run") as run:
                api.side_effect = [{"tree": {"sha": "old-tree"}}, {"sha": "blob"},
                                   {"sha": "new-tree"}, {"sha": "commit"}, None]
                generated.publish(artifact)
                self.assertEqual(verify.call_count, 2)
                self.assertEqual(api.call_args.args, (
                    f"{generated.ROOT}/git/refs/heads/{generated.BRANCH}", "PATCH",
                    {"sha": "commit", "force": False}))
                self.assertEqual(api.call_args_list[2].args[2]["tree"][0]["mode"], "100644")
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
