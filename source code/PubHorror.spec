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
    (os.path.join('app', 'assets'),              os.path.join('app', 'assets')),
]

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
    icon=os.path.join('app', 'assets', 'PubHorrorIcon.ico'),
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