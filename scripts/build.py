#!/usr/bin/env python3
"""Build Liuying for the current operating system with PyInstaller.

Mac:
    python3 scripts/build.py
    -> dist/Liuying.app

Windows (must run on a Windows machine or GitHub Actions):
    python scripts/build.py
    -> dist/Liuying/Liuying.exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.check_call(command, cwd=ROOT)


def main() -> None:
    python = sys.executable
    run([python, str(ROOT / "scripts" / "make_icon.py")])
    run([python, str(ROOT / "scripts" / "fetch_ffmpeg.py")])
    run(
        [
            python,
            "-m",
            "PyInstaller",
            str(ROOT / "Liuying.spec"),
            "--noconfirm",
            "--clean",
        ]
    )
    dist = ROOT / "dist"
    if sys.platform == "darwin":
        app = dist / "Liuying.app"
        print(f"\nmacOS app: {app}")
        print("Unsigned. If macOS says the app is damaged, run:")
        print(f'  xattr -cr "{app}"')
    else:
        folder = dist / "Liuying"
        print(f"\nWindows folder: {folder}")
        print("Ship the whole Liuying folder. Users run Liuying.exe.")
        print("Requires Microsoft Edge WebView2 (included on most Win10/11 PCs).")


if __name__ == "__main__":
    os.chdir(ROOT)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    main()
