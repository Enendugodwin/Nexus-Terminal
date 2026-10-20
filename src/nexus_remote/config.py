"""Application paths and runtime configuration.

Nothing in this module touches the network or the credential vault; it only
resolves where local state lives so tests can redirect it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Nexus Remote"
APP_SLUG = "nexus-remote"
APP_VERSION = "0.1.0"
ORG_NAME = "NexusRemote"

#: Environment variable that overrides the local data directory (used by tests
#: and portable installs).
HOME_ENV_VAR = "NEXUS_REMOTE_HOME"

DEFAULT_SSH_PORT = 22
DEFAULT_RDP_PORT = 3389
DEFAULT_SMB_PORT = 445
DEFAULT_SFTP_PORT = 22


def app_data_dir() -> Path:
    """Return the directory holding the database, logs and local state."""
    override = os.environ.get(HOME_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "NexusRemote"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_SLUG
    return Path.home() / f".{APP_SLUG}"


@dataclass(frozen=True)
class Paths:
    """Resolved application paths."""

    root: Path

    @property
    def database(self) -> Path:
        return self.root / "nexus.db"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def known_hosts(self) -> Path:
        return self.root / "known_hosts"

    @property
    def catalog(self) -> Path:
        """Downloaded vendor command catalog (absent until an update succeeds)."""
        return self.root / "catalog.json"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    def ensure(self) -> "Paths":
        for path in (self.root, self.logs, self.exports):
            path.mkdir(parents=True, exist_ok=True)
        return self


def default_paths() -> Paths:
    return Paths(app_data_dir())
