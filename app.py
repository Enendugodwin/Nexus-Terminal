"""Convenience launcher for running Nexus Remote from a source checkout."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nexus_remote.main import main

if __name__ == "__main__":
    raise SystemExit(main())
