#!/usr/bin/env python3
"""Generate app icons (PNG / ICO / ICNS) from web/icon.png."""

from __future__ import annotations

import io
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SOURCE = ROOT / "web" / "icon.png"


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _ico(png: bytes, size: int) -> bytes:
    entry = struct.pack(
        "<BBBBHHII",
        size if size < 256 else 0,
        size if size < 256 else 0,
        0,
        0,
        1,
        32,
        len(png),
        22,
    )
    return struct.pack("<HHH", 0, 1, 1) + entry + png


def _write_icns(png_1024: bytes, dest: Path) -> None:
    if sys.platform != "darwin":
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        src = Path(tmp) / "icon.png"
        src.write_bytes(png_1024)
        mapping = [
            (16, "icon_16x16.png"),
            (32, "icon_16x16@2x.png"),
            (32, "icon_32x32.png"),
            (64, "icon_32x32@2x.png"),
            (128, "icon_128x128.png"),
            (256, "icon_128x128@2x.png"),
            (256, "icon_256x256.png"),
            (512, "icon_256x256@2x.png"),
            (512, "icon_512x512.png"),
            (1024, "icon_512x512@2x.png"),
        ]
        for dim, name in mapping:
            subprocess.run(
                ["sips", "-z", str(dim), str(dim), str(src), "--out", str(iconset / name)],
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(dest)],
            check=True,
            capture_output=True,
        )


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"missing source icon: {SOURCE}")

    ASSETS.mkdir(parents=True, exist_ok=True)
    src = Image.open(SOURCE).convert("RGBA")
    icon_256 = src.resize((256, 256), Image.Resampling.LANCZOS)
    icon_1024 = src.resize((1024, 1024), Image.Resampling.LANCZOS)

    src.save(ASSETS / "icon.png")
    png_256 = _png_bytes(icon_256)
    (ASSETS / "icon.ico").write_bytes(_ico(png_256, 256))

    icns = ASSETS / "icon.icns"
    try:
        _write_icns(_png_bytes(icon_1024), icns)
    except Exception:
        icns.unlink(missing_ok=True)

    print(f"wrote assets from {SOURCE}")


if __name__ == "__main__":
    main()
