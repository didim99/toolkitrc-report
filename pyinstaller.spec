# -*- mode: python ; coding: utf-8 -*-

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyInstaller.building.api import *
    from PyInstaller.building.build_main import Analysis


parser = argparse.ArgumentParser()
parser.add_argument("-m", "--mode", help="Building mode",
                    choices=['onedir', 'onefile'], default="onedir")
parser.add_argument("-q", "--no-console", help="Disable console",
                    action="store_true")
options = parser.parse_args()

build_mode = options.mode
use_console = not options.no_console
project_name = 'toolkitrc-report'

icon = 'docs/icon.png'
if not Path(icon).is_file():
    icon = None

a = Analysis(
    ['toolkitrc_report/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

if build_mode == 'onedir':
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=project_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        icon=icon,
        console=use_console,
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
        name=project_name,
    )

elif build_mode == 'onefile':
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        name=project_name,
        debug=False,
        strip=False,
        upx=True,
        runtime_tmpdir=None,
        icon=icon,
        console=use_console,
    )
