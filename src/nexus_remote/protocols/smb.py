"""SMB file access (SMB 2/3 only).

Uses the ``smbprotocol`` package when it is installed. SMB1 is never offered:
the library does not implement it, and no fallback is attempted. When the
package is missing the adapter reports a clear, actionable error instead of
failing at import time.

Paths handed to and returned from this adapter are share-relative and use
forward slashes (``""`` is the share root, ``"logs/app.txt"`` a file below it).
The adapter converts them to UNC paths internally, so the file manager can use
exactly the same path handling it uses for SFTP.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nexus_remote.connections.interfaces import (
    ConnectionErrorKind,
    ConnectionTarget,
    ConnectResult,
    ProtocolAdapter,
    ProtocolError,
)
from nexus_remote.util import CHUNK_SIZE

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


class TransferCancelled(Exception):
    """Raised internally when the caller cancels a transfer."""


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

    @property
    def permissions(self) -> str:
        """SMB ACLs are not translated; the UI shows the size instead."""
        return ""

    def as_row(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": "dir" if self.is_dir else "file",
            "size": self.size,
            "modified": self.mtime,
        }


@dataclass
class SMBTransferResult:
    direction: str
    local_path: str
    remote_path: str
    bytes_transferred: int = 0
    total_bytes: int = 0
    resumed_from: int = 0
    cancelled: bool = False
    message: str = ""

    @property
    def percent(self) -> float:
        if not self.total_bytes:
            return 0.0
        return min(100.0, (self.bytes_transferred / self.total_bytes) * 100.0)


class SMBAdapter(ProtocolAdapter):
    protocol = "smb"

    def __init__(self) -> None:
        super().__init__()
        self._host: str = ""
        self._share: str = ""
        self._port: int = 445
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

        share = str(target.options.get("share") or "").strip().strip("\\/")
        if not share:
            raise ProtocolError(
                "no share was specified for this SMB connection",
                kind=ConnectionErrorKind.POLICY,
                detail="set the share on the device's SMB connection profile",
            )
        encrypt = bool(target.options.get("encrypt", True))
        port = int(target.port or 445)
        try:
            smbclient.register_session(
                target.host,
                username=target.username or None,
                password=target.secret,
                port=port,
                encrypt=encrypt,
                require_secure_negotiate=True,
            )
        except Exception as exc:
            raise _smb_error(f"could not open an SMB session to {target.host}", exc) from exc
        self._host = target.host
        self._share = share
        self._port = port
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

                smbclient.delete_session(self._host, port=self._port)
            except Exception:  # pragma: no cover - best effort
                pass
        self._registered = False
        self._connected = False

    # -- paths --------------------------------------------------------------
    def _relative(self, path: str) -> str:
        """Normalise an operator-supplied path to a share-relative one."""
        text = str(path or "").replace("\\", "/").strip()
        while text.startswith("./"):
            text = text[2:]
        if text in (".", "/"):
            return ""
        return text.strip("/")

    def _unc(self, path: str = "") -> str:
        if path.startswith("\\\\"):
            return path
        cleaned = self._relative(path)
        return f"\\\\{self._host}\\{self._share}" + (f"\\{cleaned}".replace("/", "\\") if cleaned else "")

    @staticmethod
    def _join(directory: str, name: str) -> str:
        return f"{directory}/{name}" if directory else name

    def _require_connection(self) -> None:
        if not self._connected:
            raise ProtocolError(
                "the SMB share is not open",
                kind=ConnectionErrorKind.PROTOCOL,
                detail="connect the SMB session before browsing it",
            )

    # -- browsing -----------------------------------------------------------
    def listdir(self, path: str = "") -> list[SMBEntry]:
        import smbclient

        self._require_connection()
        base = self._relative(path)
        try:
            names = smbclient.listdir(self._unc(base))
        except Exception as exc:
            raise _smb_error(f"could not list {self._unc(base)}", exc) from exc
        entries: list[SMBEntry] = []
        for name in names:
            if name in (".", ".."):
                continue
            full = self._join(base, name)
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

    def stat(self, path: str) -> SMBEntry:
        import smbclient

        self._require_connection()
        relative = self._relative(path)
        try:
            stats = smbclient.stat(self._unc(relative))
        except Exception as exc:
            raise _smb_error(f"could not stat {self._unc(relative)}", exc) from exc
        return SMBEntry(
            name=relative.rsplit("/", 1)[-1] or self._share,
            path=relative,
            size=int(stats.st_size),
            is_dir=bool(stats.st_is_dir),
            mtime=float(stats.st_mtime),
        )

    def mkdir(self, path: str) -> None:
        import smbclient

        self._require_connection()
        target = self._unc(path)
        try:
            smbclient.mkdir(target)
        except Exception as exc:
            raise _smb_error(f"could not create {target}", exc) from exc

    def remove(self, path: str) -> None:
        import smbclient

        self._require_connection()
        target = self._unc(path)
        try:
            smbclient.remove(target)
        except Exception as exc:
            raise _smb_error(f"could not delete {target}", exc) from exc

    def rmdir(self, path: str) -> None:
        import smbclient

        self._require_connection()
        target = self._unc(path)
        try:
            smbclient.rmdir(target)
        except Exception as exc:
            raise _smb_error(f"could not remove directory {target}", exc) from exc

    def rename(self, source: str, destination: str) -> None:
        import smbclient

        self._require_connection()
        try:
            smbclient.rename(self._unc(source), self._unc(destination))
        except Exception as exc:
            raise _smb_error(f"could not rename {self._unc(source)}", exc) from exc

    # -- transfers ----------------------------------------------------------
    def upload(
        self,
        local_path: str | os.PathLike[str],
        remote_path: str,
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
        resume: bool = True,
        chunk_size: int = CHUNK_SIZE,
    ) -> SMBTransferResult:
        import smbclient

        self._require_connection()
        local = Path(local_path)
        if not local.is_file():
            raise ProtocolError(f"{local} is not a file", kind=ConnectionErrorKind.UNKNOWN)
        relative = self._relative(remote_path)
        total = local.stat().st_size
        offset = 0
        if resume:
            try:
                remote_size = int(smbclient.stat(self._unc(relative)).st_size)
            except Exception:
                remote_size = 0
            if 0 < remote_size <= total:
                offset = remote_size
        result = SMBTransferResult(
            direction="upload",
            local_path=str(local),
            remote_path=relative,
            total_bytes=total,
            resumed_from=offset,
            bytes_transferred=offset,
        )
        mode = "r+b" if offset else "wb"
        try:
            with open(local, "rb") as source:
                remote = smbclient.open_file(self._unc(relative), mode=mode)
                with remote:
                    if offset:
                        source.seek(offset)
                        remote.seek(offset)
                    transferred = offset
                    while True:
                        if cancel is not None and cancel():
                            raise TransferCancelled()
                        block = source.read(chunk_size)
                        if not block:
                            break
                        remote.write(block)
                        transferred += len(block)
                        result.bytes_transferred = transferred
                        if progress is not None:
                            progress(transferred, total)
        except TransferCancelled:
            result.cancelled = True
            result.message = "transfer cancelled by the user"
            return result
        except Exception as exc:
            raise _smb_error(f"upload to {self._unc(relative)} failed", exc) from exc
        result.message = f"uploaded {total} bytes to \\\\{self._host}\\{self._share}\\{relative}".rstrip("\\")
        return result

    def download(
        self,
        remote_path: str,
        local_path: str | os.PathLike[str],
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
        resume: bool = True,
        chunk_size: int = CHUNK_SIZE,
    ) -> SMBTransferResult:
        import smbclient

        self._require_connection()
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        relative = self._relative(remote_path)
        try:
            total = int(smbclient.stat(self._unc(relative)).st_size)
        except Exception as exc:
            raise _smb_error(f"could not stat {self._unc(relative)}", exc) from exc
        offset = 0
        if resume and local.exists():
            existing = local.stat().st_size
            if 0 < existing <= total:
                offset = existing
        result = SMBTransferResult(
            direction="download",
            local_path=str(local),
            remote_path=relative,
            total_bytes=total,
            resumed_from=offset,
            bytes_transferred=offset,
        )
        mode = "r+b" if offset else "wb"
        try:
            with open(local, mode) as target:
                remote = smbclient.open_file(self._unc(relative), mode="rb")
                with remote:
                    if offset:
                        remote.seek(offset)
                        target.seek(offset)
                    transferred = offset
                    while True:
                        if cancel is not None and cancel():
                            raise TransferCancelled()
                        block = remote.read(chunk_size)
                        if not block:
                            break
                        target.write(block)
                        transferred += len(block)
                        result.bytes_transferred = transferred
                        if progress is not None:
                            progress(transferred, total)
        except TransferCancelled:
            result.cancelled = True
            result.message = "transfer cancelled by the user"
            return result
        except Exception as exc:
            raise _smb_error(f"download of {self._unc(relative)} failed", exc) from exc
        result.message = f"downloaded {total} bytes from \\\\{self._host}\\{self._share}\\{relative}".rstrip("\\")
        return result


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
