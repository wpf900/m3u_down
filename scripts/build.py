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
        print(f"\nMac 应用: {app}")
        print("未签名。别人电脑上若提示损坏，请右键打开，或执行：")
        print(f'  xattr -cr "{app}"')
    else:
        folder = dist / "Liuying"
        print(f"\nWindows 目录: {folder}")
        print("把整个 Liuying 文件夹发给用户，双击 Liuying.exe。")
        print("需要系统已安装 Microsoft Edge WebView2（Win10/11 一般都有）。")


if __name__ == "__main__":
    os.chdir(ROOT)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    main()
