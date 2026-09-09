# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

用法（在项目根目录执行）：
    pip install pyinstaller
    pyinstaller --noconfirm --distpath dist --workpath build/pyi build/novelfound.spec

默认产出 **onedir** 目录模式（推荐）：
    dist/NovelFound/NovelFound.exe      双击即可运行（整个 NovelFound 文件夹一起分发）

如需单文件模式（体积一样、启动稍慢，需要能写入系统临时目录）：
    Windows:  set NOVELFOUND_ONEFILE=1 && pyinstaller --noconfirm --distpath dist --workpath build/pyi build/novelfound.spec
    macOS/Linux:  NOVELFOUND_ONEFILE=1 pyinstaller --noconfirm --distpath dist --workpath build/pyi build/novelfound.spec
    产出：dist/NovelFound.exe

两种模式都无需目标机器安装 Python。
"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
ONEFILE = os.environ.get("NOVELFOUND_ONEFILE", "").strip() == "1"

block_cipher = None

# 只打包 Qt 里真正用到的模块，避免 PyInstaller 把整个 Qt 拖进来
hiddenimports = [
    "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtWidgets",
    "bs4", "lxml", "lxml.etree", "requests", "charset_normalizer", "urllib3",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 明显用不到的重量级模块，减小体积
        "tkinter", "matplotlib", "numpy", "pandas", "PIL",
        "PyQt5.QtWebEngineWidgets", "PyQt5.QtQml", "PyQt5.QtQuick",
        "PyQt5.QtMultimedia", "PyQt5.QtBluetooth", "PyQt5.Qt3DCore",
        "PyQt5.QtNetworkAuth", "PyQt5.QtWebSockets", "PyQt5.QtWebChannel",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="NovelFound",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,          # GUI 程序，不弹黑框
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="NovelFound",
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
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="NovelFound",
    )

# macOS 需要 .app 包；Windows 上该分支不会执行
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="NovelFound.app",
        icon=None,
        bundle_identifier="com.novelfound.reader",
        info_plist={
            "CFBundleName": "NovelFound",
            "CFBundleDisplayName": "小说搜索阅读器",
            "NSHighResolutionCapable": True,
        },
    )
