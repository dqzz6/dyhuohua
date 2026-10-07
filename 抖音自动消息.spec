# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all("tzdata")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# PySide6 的 Qt6Core 依赖 Windows ICU。显式复制系统匹配版本，
# 避免打包程序误加载路径中的旧版 icuuc.dll。
system_root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
for name in ("icu.dll", "icuin.dll", "icuuc.dll"):
    source = system_root / name
    if source.exists():
        binaries.append((str(source), "."))
        binaries.append((str(source), "PySide6"))


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='抖音自动消息',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='抖音自动消息',
)
