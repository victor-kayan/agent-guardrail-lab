"""Check that the wheel ships the frontend used by the local dashboard."""

import sys
from pathlib import Path
from zipfile import ZipFile

directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
wheels = list(directory.glob("*.whl"))
if not wheels:
    raise SystemExit("No wheel found; run uv build --wheel first")
wheel = max(wheels, key=lambda path: path.stat().st_mtime)
with ZipFile(wheel) as archive:
    for name in ("index.html", "app.js", "styles.css"):
        asset = f"guardrail_lab/web/{name}"
        if asset not in archive.namelist() or not archive.read(asset):
            raise SystemExit(f"Missing or empty frontend asset: {asset}")
print(f"Frontend assets verified: {wheel.name}")
