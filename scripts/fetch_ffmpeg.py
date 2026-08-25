#!/usr/bin/env python3
"""Download a static ffmpeg binary for the current OS/arch into vendor/."""

from __future__ import annotations

import gzip
import platform
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
RELEASE = "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"


def asset_name() -> str:
    system = sys.platform
    machine = platform.machine().lower()
    if system == "win32":
        return "ffmpeg-win32-x64.gz"
    if system == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "ffmpeg-darwin-arm64.gz"
        return "ffmpeg-darwin-x64.gz"
    if machine in {"arm64", "aarch64"}:
        return "ffmpeg-linux-arm64.gz"
    return "ffmpeg-linux-x64.gz"


def main() -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    dest = VENDOR / name
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"already have {dest}")
        return
    url = f"{RELEASE}/{asset_name()}"
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        payload = response.read()
    dest.write_bytes(gzip.decompress(payload))
    dest.chmod(dest.stat().st_mode | 0o111)
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
