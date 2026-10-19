"""SMB file access (SMB 2/3 only).

Uses the ``smbprotocol`` package when it is installed. SMB1 is never offered:
the library does not implement it, and no fallback is attempted. When the
package is missing the adapter reports a clear, actionable error instead of
failing at import time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from nexus_remote.connections.interfaces import (
    ConnectionErrorKind,
    ConnectionTarget,
    ConnectResult,
    ProtocolAdapter,
    ProtocolError,
)
from nexus_remote.util import CHUNK_SIZE

ProgressCallback = Callable[[int, int], None]


def smb_available() -> bool:
    try:
        import smbclient  # noqa: F401
    except Exception:
        return False
    return True


@dataclass(frozen=True)
class SMBEntry:
    name: str
    path: str
    size: int = 0
    is_dir: bool = False
    mtime: float = 0.0

    def as_row(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": "dir" if self.is_dir else "file",
            "size": self.size,
            "modified": self.mtime,
        }


class SMBAdapter(ProtocolAdapter):
    protocol = "smb"

    def __init__(self) -> None:
        super().__init__()
        self._host: str = ""
        self._share: str = ""
        self._registered = False

    # -- lifecycle ----------------------------------------------------------
    def connect(self, target: ConnectionTarget) -> ConnectResult:
        if not smb_available():
            raise ProtocolError(
                "SMB support requires the 'smbprotocol' package",
                kind=ConnectionErrorKind.POLICY,
                detail="install the optional dependency: pip install nexus-remote[smb]",
            )
        import smbclient

        share = str(target.options.get("share") or "").strip()
        if not share:
            raise ProtocolError(
                "no share was specified for this SMB connection",
                kind=ConnectionErrorKind.POLICY,
                detail="set options.share in the device's SMB connection profile",
            )
        encrypt = bool(target.options.get("encrypt", True))
        try:
            smbclient.register_session(
                target.host,
                username=target.username or None,
                password=target.secret,
                port=int(target.port or 445),
                encrypt=encrypt,
                require_secure_negotiate=True,
            )
        except Exception as exc:
            raise _smb_error(f"could not open an SMB session to {target.host}", exc) from exc
        self._host = target.host
        self._share = share
        self._registered = True
        self._connected = True
        return ConnectResult(
            ok=True,
            message=f"SMB session opened to \\\\{target.host}\\{share}",
            auth_method="ntlm",
            metadata={"share": share, "encrypt": encrypt},
        )

    def close(self) -> None:
        if self._registered:
            try:
                import smbclient

                smbclient.delete_session(self._host, port=445)
            except Exception:  # pragma: no cover - best effort
                pass
        self._registered = False
        self._connected = False

    # -- browsing -----------------------------------------------------------
    def _unc(self, path: str = "") -> str:
        if path.startswith("\\\\"):
            return path
        cleaned = path.replace("/", "\\").strip("\\")
        return f"\\\\{self._host}\\{self._share}" + (f"\\{cleaned}" if cleaned else "")

    def listdir(self, path: str = "") -> list[SMBEntry]:
        import smbclient

        base = path
        try:
            names = smbclient.listdir(self._unc(path))
        except Exception as exc:
            raise _smb_error(f"could not list {self._unc(path)}", exc) from exc
        entries: list[SMBEntry] = []
        for name in names:
            if name in (".", ".."):
                continue
            full = f"{base.rstrip('/')}/{name}" if base else name
            try:
                stats = smbclient.stat(self._unc(full))
                is_dir = bool(stats.st_is_dir)
                size = int(stats.st_size)
                mtime = float(stats.st_mtime)
            except Exception:  # pragma: no cover - per-entry failures are tolerated
                is_dir, size, mtime = False, 0, 0.0
            entries.append(SMBEntry(name=name, path=full, size=size, is_dir=is_dir, mtime=mtime))
        entries.sort(key=lambda entry: (not entry.is_dir, entry.name.casefold()))
        return entries

    def upload(
        self,
        local_path: str | os.PathLike[str],
        remote_path: str,
        *,
        progress: ProgressCallback | None = None,
        chunk_size: int = CHUNK_SIZE,
    ) -> int:
        import smbclient

        local = Path(local_path)
        total = local.stat().st_size
        transferred = 0
        try:
            with open(local, "rb") as source, smbclient.open_file(self._unc(remote_path), mode="wb") as target:
                while True:
                    block = source.read(chunk_size)
                    if not block:
                        break
                    target.write(block)
                    transferred += len(block)
                    if progress:
                        progress(transferred, total)
        except Exception as exc:
            raise _smb_error(f"upload to {self._unc(remote_path)} failed", exc) from exc
        return transferred

    def download(
        self,
        remote_path: str,
        local_path: str | os.PathLike[str],
        *,
        progress: ProgressCallback | None = None,
        chunk_size: int = CHUNK_SIZE,
    ) -> int:
        import smbclient

        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        transferred = 0
        total = int(smbclient.stat(self._unc(remote_path)).st_size)
        try:
            with smbclient.open_file(self._unc(remote_path), mode="rb") as source, open(local, "wb") as target:
                while True:
                    block = source.read(chunk_size)
                    if not block:
                        break
                    target.write(block)
                    transferred += len(block)
                    if progress:
                        progress(transferred, total)
        except Exception as exc:
            raise _smb_error(f"download of {self._unc(remote_path)} failed", exc) from exc
        return transferred


def _smb_error(message: str, exc: Exception) -> ProtocolError:
    text = str(exc)
    lowered = text.lower()
    if "access" in lowered or "logon" in lowered or "auth" in lowered:
        kind = ConnectionErrorKind.AUTH
    elif "signing" in lowered or "encrypt" in lowered:
        kind = ConnectionErrorKind.POLICY
    elif "connect" in lowered or "timeout" in lowered:
        kind = ConnectionErrorKind.TIMEOUT
    else:
        kind = ConnectionErrorKind.UNKNOWN
    return ProtocolError(f"{message}: {text}", kind=kind, detail=text)
