# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Nexus Remote desktop app.

Build from the repository root:

    pyinstaller packaging/nexus_remote.spec --noconfirm --clean

The result is a one-directory bundle in ``dist/NexusRemote``. Qt plugins are
collected automatically; migrations are included as data files.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"

analysis = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (
            str(SRC / "nexus_remote" / "storage" / "migrations"),
            "nexus_remote/storage/migrations",
        ),
    ],
    hiddenimports=[
        # keyring resolves backends through entry points, which PyInstaller
        # cannot see statically.
        "keyring.backends.Windows",
        "keyring.backends.fail",
        "pyte",
        "paramiko",
        "cryptography",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="NexusRemote",
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
)

# One-directory layout: faster startup than one-file and easier to patch.
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="NexusRemote",
)
