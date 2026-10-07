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


def _normalized(value):
    return str(value or "").replace("\\", "/").lower()


def _drop_binary(destination):
    path = _normalized(destination)
    name = Path(path).name

    # 只保留 Qt 核心、Widgets、WebEngine 和 Quick 运行所需模块。
    dropped_prefixes = (
        "qt63d",
        "qt6charts",
        "qt6datavisualization",
        "qt6graphs",
        "qt6location",
        "qt6multimedia",
        "qt6pdf",
        "qt6remoteobjects",
        "qt6scxml",
        "qt6spatialaudio",
        "qt6sql",
        "qt6statemachine",
        "qt6test",
        "qt6virtualkeyboard",
        "qt6webenginequick",
        "qt6quick3d",
        "qt6quickcontrols2",
        "qt6quickdialogs2",
        "qt6quickparticles",
        "qt6quickeffects",
        "qt6quickshapes",
        "qt6quicktest",
        "qt6quickvectorimagegenerator",
        "qt6labs",
        "qt6networkauth",
        "qt6opcua",
        "qt6sensors",
        "qt6serialport",
        "qt6speech",
        "qt6texttospeech",
        "qt6webview",
        "qt6websockets",
    )
    if name.startswith(dropped_prefixes):
        return True

    # QtCore 的 ICU 只保留 Qt DLL 同目录副本，避免重复约 34MB。
    if name in {"icu.dll", "icuin.dll", "icuuc.dll", "icudt78.dll"}:
        return not path.startswith("pyside6/")

    # 不打包 Chrome DevTools 前端资源；CDP 调试协议本身仍可正常使用。
    if "qtwebengine_devtools_resources" in name:
        return True
    return False


def _drop_data(destination):
    path = _normalized(destination)
    name = Path(path).name

    if _drop_binary(destination):
        return True

    qml_drop_parts = (
        "/qml/qt3d/",
        "/qml/qtcharts/",
        "/qml/qtdatavisualization/",
        "/qml/qtgraphs/",
        "/qml/qtlocation/",
        "/qml/qtmultimedia/",
        "/qml/qtpdf/",
        "/qml/qtquick3d/",
        "/qml/qtscxml/",
        "/qml/qtsensors/",
        "/qml/qtsql/",
        "/qml/qttest/",
        "/qml/qtvirtualkeyboard/",
        "/qml/qtwebengine/",
        "/qml/qtwebview/",
        "/qml/qtopcua/",
        "/qml/qtremoteobjects/",
        "/qml/qtshadertools/",
        "/qml/qtspeech/",
        "/qml/qtspatialaudio/",
        "/qml/qtquick/controls/",
    )
    if any(part in f"/{path}" for part in qml_drop_parts):
        return True

    if "/translations/" in path:
        if "/qtwebengine_locales/" in path:
            return name not in {"zh-cn.pak", "en-us.pak"}
        return True
    return False


a.binaries = [entry for entry in a.binaries if not _drop_data(entry[0])]
a.datas = [entry for entry in a.datas if not _drop_data(entry[0])]

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
