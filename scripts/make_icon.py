#!/usr/bin/env python3
"""Generate app icons (PNG / ICO / ICNS) without extra dependencies."""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
WEB = ROOT / "web"


def _png(size: int) -> bytes:
    def px(x: int, y: int) -> tuple[int, int, int, int]:
        cx = cy = (size - 1) / 2
        dx, dy = x - cx, y - cy
        r = (dx * dx + dy * dy) ** 0.5
        rad = size * 0.46
        if r > rad:
            return (0, 0, 0, 0)
        t = r / rad
        # pistachio rounded mark
        g = int(183 + (143 - 183) * t)
        b = int(164 + (120 - 164) * t)
        rr = int(183 + (120 - 183) * t * 0.2)
        # inner lemon chevron (play)
        nx, ny = dx / (size * 0.18), dy / (size * 0.22)
        if -0.55 < nx < 0.85 and abs(ny) < 0.9 - nx * 0.55:
            return (248, 243, 196, 255)
        return (rr, g, b, 255)

    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            raw.extend(px(x, y))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
            chunk(b"IEND", b""),
        ]
    )


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
    ASSETS.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    png_256 = _png(256)
    png_1024 = _png(1024)
    (ASSETS / "icon.png").write_bytes(png_256)
    (WEB / "icon.png").write_bytes(_png(64))
    (ASSETS / "icon.ico").write_bytes(_ico(png_256, 256))
    icns = ASSETS / "icon.icns"
    try:
        _write_icns(png_1024, icns)
    except Exception:
        icns.unlink(missing_ok=True)
    print(f"wrote {ASSETS / 'icon.png'}")


if __name__ == "__main__":
    main()
