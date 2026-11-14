# -*- mode: python ; coding: utf-8 -*-
#
# PubHorror — PyInstaller .spec file
#
# HOW TO USE:
#   Run build.bat  (recommended)          — handles venv, cleaning, and UPX.
#   OR manually:  pyinstaller PubHorror.spec
#
# EXPECTED SOURCE LAYOUT:
#
#   project\
#     main.py               ← entry point
#     PubHorror.spec        ← this file
#     build.bat
#     app\
#       index.html
#       assets\
#         OpenDyslexic-Regular.otf
#         PubHorrorIcon.ico            ← optional but nice
#
# The compiled output lands in:
#   dist\PubHorror\
#     PubHorror.exe
#     _internal\            ← all DLLs, .pyd, etc. (PyInstaller 6+)
#     app\
#       index.html
#       assets\

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# ── Collect pywebview's own data files (JS bridge etc.) ──────
webview_datas = collect_data_files('webview')

# ── App source files to bundle into _MEIPASS\app\ ────────────
# main.py expects:  sys._MEIPASS / "app" / "index.html"
#                   sys._MEIPASS / "app" / "assets" / *
app_datas = [
    (os.path.join('app', 'index.html'),          'app'),
]
# Only bundle assets/ if it exists, so the build doesn't fail when it's missing
if os.path.isdir(os.path.join('app', 'assets')):
    app_datas.append((os.path.join('app', 'assets'), os.path.join('app', 'assets')))
else:
    print('WARNING: app/assets not found - building without fonts/icon')
# Small vendored browser libraries (QR code for the sharing settings)
if os.path.isdir(os.path.join('app', 'vendor')):
    app_datas.append((os.path.join('app', 'vendor'), os.path.join('app', 'vendor')))
# Watch Party sync client, shared by the desktop app and the sidecar's phone page
if os.path.isdir(os.path.join('app', 'shared')):
    app_datas.append((os.path.join('app', 'shared'), os.path.join('app', 'shared')))
# The Node sidecar (sidecar\) is NOT bundled by PyInstaller; the installer copies it next to PubHorror.exe.

_icon = os.path.join('app', 'assets', 'PubHorrorIcon.ico')
_icon = _icon if os.path.exists(_icon) else None

# ── Windows file details (Properties → Details) ───────────
# An exe with no publisher/product/version info looks more suspicious to antivirus heuristics.
# The version comes from PubHorror.iss (MyAppVersion), so it's only ever set in one place.
import re
from PyInstaller.utils.win32.versioninfo import (VSVersionInfo, FixedFileInfo, StringFileInfo,
                                                  StringTable, StringStruct, VarFileInfo, VarStruct)
with open('PubHorror.iss', encoding='utf-8') as _f:
    _m = re.search(r'#define\s+MyAppVersion\s+"([\d.]+)"', _f.read())
_ver = _m.group(1) if _m else '0.0.0'
_vt = tuple((list(map(int, _ver.split('.'))) + [0, 0, 0, 0])[:4])
_version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_vt, prodvers=_vt, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'TrinketGoblin'),
            StringStruct('FileDescription', 'PubHorror - horror movie library'),
            StringStruct('FileVersion', _ver),
            StringStruct('InternalName', 'PubHorror'),
            StringStruct('LegalCopyright', 'Released under CC0 1.0'),
            StringStruct('OriginalFilename', 'PubHorror.exe'),
            StringStruct('ProductName', 'PubHorror'),
            StringStruct('ProductVersion', _ver),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ])

all_datas = webview_datas + app_datas

# ── Hidden imports ────────────────────────────────────────────
# PyInstaller misses these because they are imported at runtime
# or loaded via plugin/entry-point machinery.
hidden_imports = [
    # pywebview core
    'webview',
    'webview.window',
    'webview.event',
    'webview.http',
    'webview.js',
    'webview.js.css',
    'webview.js.dom',
    'webview.util',
    'webview.screen',
    'webview.menu',
    'webview.platforms',
    'webview.platforms.edgechromium',   # <-- the GUI backend we use

    # pywebview[http] dependencies — required for local file serving
    'bottle',
    'multipart',

    # requests + dependencies (used for TMDB calls)
    'requests',
    'requests.adapters',
    'requests.auth',
    'requests.cookies',
    'requests.exceptions',
    'requests.models',
    'requests.sessions',
    'requests.structures',
    'requests.utils',
    'certifi',
    'charset_normalizer',
    'charset_normalizer.md__mypyc',
    'idna',
    'urllib3',
    'urllib3.contrib',
    'urllib3.util',

    # stdlib used dynamically
    'ctypes',
    'ctypes.wintypes',
    'json',
    'uuid',
    'shutil',
    're',
    'string',
    'logging',
    'logging.handlers',
]

# ── Analysis ──────────────────────────────────────────────────
a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=all_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── PYZ archive ───────────────────────────────────────────────
pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

# ── EXE ───────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PubHorror',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # Disabled — UPX corrupts pywebview/WebView2 bridge DLLs
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
    version=_version_info,
)

# ── COLLECT (onedir bundle) ───────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,          # Disabled — UPX corrupts pywebview/WebView2 bridge DLLs
    upx_exclude=[],
    name='PubHorror',
)