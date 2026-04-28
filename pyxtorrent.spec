# PyInstaller spec for pyxTorrent.
# Build: pyinstaller pyxtorrent.spec
# Output: dist/pyxTorrent.exe (Windows) or dist/pyxTorrent (other)

# ruff: noqa
# pylint: disable=undefined-variable

import os

block_cipher = None

# Bundle the engines/ folder as data so search.py can dynamically import each
# plugin at runtime via sys._MEIPASS.
datas = [
    ("engines", "engines"),
]

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
    binaries=[],
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
