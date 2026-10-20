"""Generate the update manifest consumed by Nexus Remote.

Run after building the installer:

    python packaging/make_manifest.py ^
        --url https://downloads.example.com/NexusRemoteSetup-0.3.0.exe ^
        --notes "What changed"

The version defaults to ``pyproject.toml`` and the installer to
``dist/NexusRemoteSetup-<version>.exe``. The script computes the size and
SHA-256, validates the HTTPS URL, and writes ``dist/latest.json`` (override with
``--output``). Upload that JSON next to the setup file.

This script is packaging-only; it is not bundled into the application.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create dist/latest.json for the updater")
    parser.add_argument("--url", required=True, help="HTTPS URL of the setup .exe")
    parser.add_argument("--version", default="", help="version being published")
    parser.add_argument("--installer", type=Path, default=None, help="path to the setup .exe")
    parser.add_argument("--notes", default="", help="release notes shown to users")
    parser.add_argument("--published", default="", help="free-form release date")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "latest.json")
    args = parser.parse_args(argv)

    version = args.version.strip() or project_version()
    installer = args.installer or (ROOT / "dist" / f"NexusRemoteSetup-{version}.exe")
    parsed = urlparse(args.url)
    if parsed.scheme != "https" or not parsed.netloc:
        print("error: --url must be an absolute https:// URL", file=sys.stderr)
        return 2
    if not parsed.path.lower().endswith(".exe"):
        print("error: --url must point to a Windows .exe", file=sys.stderr)
        return 2
    if not installer.is_file():
        print(f"error: installer not found: {installer}", file=sys.stderr)
        return 2

    digest, size = sha256_and_size(installer)
    manifest = {
        "schema": 1,
        "version": version,
        "url": args.url,
        "sha256": digest,
        "size": size,
        "notes": args.notes[:4000],
        "published": args.published[:40],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {args.output}")
    print(f"version : {version}")
    print(f"bytes   : {size}")
    print(f"sha256  : {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
