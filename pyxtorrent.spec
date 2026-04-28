# PyInstaller spec for pyxTorrent.
# Build: pyinstaller pyxtorrent.spec
# Output: dist/pyxTorrent.exe (Windows) or dist/pyxTorrent (other)

# ruff: noqa
# pylint: disable=undefined-variable

import os
import importlib.util
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

block_cipher = None

# Bundle the engines/ folder as data so search.py can dynamically import each
# plugin at runtime via sys._MEIPASS.
datas = [
    ("engines", "engines"),
]

# libtorrent on Windows ships as a single top-level `libtorrent.pyd` (a native
# extension). PyInstaller's hidden-import scan can list it but sometimes fails
# to actually copy the .pyd into the bundle, which leaves the runtime with
# `ModuleNotFoundError: libtorrent`. Locate the file ourselves and force-bundle
# it as a binary, plus any companion DLLs the wheel installed alongside it.
binaries = []
_lt_spec = importlib.util.find_spec("libtorrent")
if _lt_spec is not None and _lt_spec.origin:
    # Single-file extension (.pyd / .so / .dylib) at site-packages root.
    binaries.append((_lt_spec.origin, "."))
    # Sweep neighbouring DLLs (boost_python, OpenSSL, etc.) from the wheel.
    _lt_dir = os.path.dirname(_lt_spec.origin)
    for _name in os.listdir(_lt_dir):
        if _name.lower().endswith(".dll"):
            _full = os.path.join(_lt_dir, _name)
            if os.path.isfile(_full):
                binaries.append((_full, "."))
# Fallback: PyInstaller helper for packages that ship .dll/.so/.dylib files.
binaries.extend(collect_dynamic_libs("libtorrent"))

hiddenimports = [
    # The engines/ folder is imported dynamically — list each so PyInstaller
    # picks them up regardless of the source-side dynamic loader.
    "helpers",
    "novaprinter",
    "piratebay",
    "eztv",
    "solidtorrents",
    "limetorrents",
    "torrentscsv",
    "torlock",
    # libtorrent is a compiled extension — pre-declare so it's pulled in even
    # when only referenced behind a try/except in downloader.py.
    "libtorrent",
]

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath("engines")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="pyxTorrent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
