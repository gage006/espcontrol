"""New device adoption, editor, and component routes stay inside the fork."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


GUARD = Path(__file__).resolve().parents[1] / "scripts/check_fork_config.py"
SLUG = "guition-esp32-p4-jc4880p443-v3"


class ForkRoutingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.files = {
            "common/addon/firmware_update.yaml": "https://gage006.github.io/espcontrol/firmware/",
            "common/device/core_infra.yaml": "https://gage006.github.io/espcontrol/webserver/",
            "src/webserver/application/firmware_metadata.ts": "https://gage006.github.io/espcontrol/firmware/",
            "scripts/firmware_release.py": "https://github.com/gage006/espcontrol/releases/tag/",
            "src/webserver/cards/media.ts": 'label: "Tile Display"',
            f"builds/{SLUG}.factory.yaml": (
                f"dashboard_import:\n  package_import_url: github://gage006/espcontrol/devices/{SLUG}/esphome.yaml@main\n"
                "web_server:\n  js_url: https://gage006.github.io/espcontrol/webserver/www.js\n"),
            f"devices/{SLUG}/esphome.yaml": "packages:\n  setup:\n    url: https://github.com/gage006/espcontrol/\n",
            f"devices/{SLUG}/device/device.yaml": 'substitutions:\n  espcontrol_component_url: "https://github.com/gage006/espcontrol"\n',
        }
        for name, content in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def run_guard(self):
        return subprocess.run([sys.executable, str(GUARD), str(self.root)],
                              text=True, capture_output=True)

    def test_fork_routes_pass_and_upstream_new_device_routes_fail(self):
        self.assertEqual(self.run_guard().returncode, 0)
        for name in (f"builds/{SLUG}.factory.yaml", f"devices/{SLUG}/esphome.yaml",
                     f"devices/{SLUG}/device/device.yaml"):
            with self.subTest(source=name):
                path = self.root / name
                path.write_text(self.files[name].replace("gage006", "jtenniswood"), encoding="utf-8")
                result = self.run_guard()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(path), result.stderr)
                path.write_text(self.files[name], encoding="utf-8")

    def test_new_production_source_is_checked_without_factory_build(self):
        (self.root / f"builds/{SLUG}.factory.yaml").unlink()
        self.assertEqual(self.run_guard().returncode, 0)
        for name in (f"devices/{SLUG}/esphome.yaml", f"devices/{SLUG}/device/device.yaml"):
            with self.subTest(source=name):
                path = self.root / name
                path.write_text(self.files[name].replace("gage006", "jtenniswood"), encoding="utf-8")
                self.assertNotEqual(self.run_guard().returncode, 0)
                path.write_text(self.files[name], encoding="utf-8")

    def test_missing_adoption_source_or_fork_editor_fails_closed(self):
        (self.root / f"devices/{SLUG}/esphome.yaml").unlink()
        self.assertNotEqual(self.run_guard().returncode, 0)
        (self.root / f"devices/{SLUG}/esphome.yaml").write_text(
            self.files[f"devices/{SLUG}/esphome.yaml"], encoding="utf-8")
        factory = self.root / f"builds/{SLUG}.factory.yaml"
        factory.write_text(self.files[f"builds/{SLUG}.factory.yaml"].replace(
            "https://gage006.github.io", "https://different-owner.github.io"), encoding="utf-8")
        self.assertNotEqual(self.run_guard().returncode, 0)


if __name__ == "__main__":
    unittest.main()
