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
        (folder / "请解压后在本目录运行.txt").write_text(
            "不要只拷贝 Liuying.exe。\n"
            "必须保留同目录下的 _internal 文件夹，然后双击 Liuying.exe。\n"
            "\n"
            "如果弹出 Python.Runtime.dll 相关错误：\n"
            "回到下载的 zip，右键 → 属性 → 勾选「解除锁定」→ 确定，\n"
            "然后删除已解压的文件夹，重新解压后再运行。\n"
            "\n"
            "如果双击没反应，安装 WebView2 后重试：\n"
            "https://go.microsoft.com/fwlink/p/?LinkId=2124703\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    os.chdir(ROOT)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    main()
