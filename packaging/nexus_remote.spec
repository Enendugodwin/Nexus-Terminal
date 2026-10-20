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
        (
            str(SRC / "nexus_remote" / "resources"),
            "nexus_remote/resources",
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
        # Serial ports are imported lazily by the serial adapter.
        "serial",
        "serial.tools",
        "serial.tools.list_ports",
        # Optional SMB support: the adapter imports smbclient lazily, so the
        # bundler needs to be told it may be present.
        "smbclient",
        "smbprotocol",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "ruff"],
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
    icon=str(SRC / "nexus_remote" / "resources" / "nexus.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
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
