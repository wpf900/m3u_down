# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH)

ffmpeg = None
for name in ("ffmpeg.exe", "ffmpeg"):
    candidate = ROOT / "vendor" / name
    if candidate.is_file():
        ffmpeg = candidate
        break

binaries = [(str(ffmpeg), ".")] if ffmpeg else []

icon = None
if sys.platform == "win32" and (ROOT / "assets" / "icon.ico").exists():
    icon = str(ROOT / "assets" / "icon.ico")
elif sys.platform == "darwin" and (ROOT / "assets" / "icon.icns").exists():
    icon = str(ROOT / "assets" / "icon.icns")
elif (ROOT / "assets" / "icon.png").exists():
    icon = str(ROOT / "assets" / "icon.png")

datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "assets"), "assets"),
]
datas += collect_data_files("webview", subdir="js")
if sys.platform == "win32":
    datas += collect_data_files("webview", subdir="lib")
    binaries += collect_dynamic_libs("webview")

hiddenimports = [
    "bottle",
    "Crypto.Cipher.AES",
    "Crypto.Util.Padding",
    "webview",
    "webview.http",
]
if sys.platform == "darwin":
    hiddenimports += ["webview.platforms.cocoa"]
elif sys.platform == "win32":
    hiddenimports += [
        "clr",
        "clr_loader",
        "pythonnet",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "webview.platforms.win32",
    ]

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Liuying",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Liuying",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Liuying.app",
        icon=icon,
        bundle_identifier="com.liuying.app",
        info_plist={
            "CFBundleName": "流影",
            "CFBundleDisplayName": "流影",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
        },
    )
