"""Access to bundled application resources (icons, images)."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(name: str) -> Path:
    """Return a path inside ``nexus_remote/resources`` (works when frozen)."""
    return Path(__file__).resolve().parent / "resources" / name


def application_icon():
    """The multi-resolution window/taskbar icon, or a null icon if missing."""
    from PySide6.QtGui import QIcon

    path = resource_path("nexus.ico")
    return QIcon(str(path)) if path.exists() else QIcon()


def set_windows_app_id(app_id: str = "NexusRemote.NexusRemote") -> None:
    """Give Windows a stable identity so the taskbar uses our icon/grouping."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:  # pragma: no cover - older Windows / restricted shell
        pass
