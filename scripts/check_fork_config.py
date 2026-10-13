"""Guard fork-owned update destinations before automated upstream merges."""
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
checks = {
    "common/addon/firmware_update.yaml": "https://gage006.github.io/espcontrol/firmware/",
    "common/device/core_infra.yaml": "https://gage006.github.io/espcontrol/webserver/",
    "src/webserver/application/firmware_metadata.ts": "https://gage006.github.io/espcontrol/firmware/",
    "scripts/firmware_release.py": "https://github.com/gage006/espcontrol/releases/tag/",
    "src/webserver/cards/media.ts": 'label: "Tile Display"',
}
for filename, expected in checks.items():
    if expected not in (root / filename).read_text(encoding="utf-8"):
        raise SystemExit(f"Fork configuration lost in {filename}: expected {expected}")
sources = {path for path in (root / "devices").glob("*/esphome.yaml")
           if not path.parent.name.endswith("-test")}
sources.update(path for path in (root / "devices").glob("*/device/device.yaml")
               if not path.parent.parent.name.endswith("-test"))
for filename in (root / "builds").glob("*.factory.yaml"):
    if "-test." in filename.name:
        continue
    slug = filename.name.removesuffix(".factory.yaml")
    text = filename.read_text(encoding="utf-8")
    if f"github://gage006/espcontrol/devices/{slug}/esphome.yaml@main" not in text:
        raise SystemExit(f"Factory adoption does not point at the fork: {filename}")
    if "https://gage006.github.io/espcontrol/webserver/" not in text:
        raise SystemExit(f"Factory editor does not point at the fork: {filename}")
    if "github://jtenniswood/espcontrol/" in text or "https://github.com/jtenniswood/espcontrol" in text:
        raise SystemExit(f"Factory adoption or component source still points at upstream: {filename}")
    sources.update([root / "devices" / slug / "esphome.yaml", root / "devices" / slug / "device/device.yaml"])
for source in sorted(sources):
    text = source.read_text(encoding="utf-8")
    if "github://jtenniswood/espcontrol/" in text or "https://github.com/jtenniswood/espcontrol" in text:
        raise SystemExit(f"Adoption or component source still points at upstream: {source}")
    if "https://github.com/gage006/espcontrol" not in text:
        raise SystemExit(f"Production package or component source does not point at the fork: {source}")
print("Fork update routing and media settings checks passed.")
