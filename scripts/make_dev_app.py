#!/usr/bin/env python3
"""Create Liuying-Dev.app for local development with a proper Dock icon."""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "Liuying-Dev.app"
CONTENTS = APP / "Contents"
MACOS = CONTENTS / "MacOS"
RESOURCES = CONTENTS / "Resources"


def main() -> None:
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "make_icon.py")])
    icns = ROOT / "assets" / "icon.icns"
    if not icns.is_file():
        raise SystemExit("missing assets/icon.icns")

    if APP.exists():
        shutil.rmtree(APP)

    MACOS.mkdir(parents=True)
    RESOURCES.mkdir(parents=True)
    shutil.copy2(icns, RESOURCES / "icon.icns")

    python = sys.executable
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.is_file():
        python = str(venv_python.resolve())

    launcher = MACOS / "Liuying-Dev"
    launcher.write_text(
        f"""#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
exec {python!r} app.py
""",
        encoding="utf-8",
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    (CONTENTS / "Info.plist").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>zh_CN</string>
  <key>CFBundleExecutable</key>
  <string>Liuying-Dev</string>
  <key>CFBundleIconFile</key>
  <string>icon</string>
  <key>CFBundleIdentifier</key>
  <string>com.liuying.dev</string>
  <key>CFBundleName</key>
  <string>流影 Dev</string>
  <key>CFBundleDisplayName</key>
  <string>流影 Dev</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0.0</string>
  <key>CFBundleVersion</key>
  <string>1.0.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
""",
        encoding="utf-8",
    )

    print(f"Created {APP}")
    print("Double-click Liuying-Dev.app to run with Dock icon.")


if __name__ == "__main__":
    main()
