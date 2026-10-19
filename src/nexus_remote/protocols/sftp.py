"""SFTP file access over the SSH transport.

Transfers stream in bounded chunks (never loading a whole file into memory),
support cancellation and resume, and report progress through a callback.
"""

from __future__ import annotations

import os
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nexus_remote.connections.interfaces import (
    ConnectionErrorKind,
    ConnectionTarget,
    ConnectResult,
    ProtocolError,
)
from nexus_remote.protocols.ssh import ParamikoSSHAdapter
from nexus_remote.util import CHUNK_SIZE

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


class TransferCancelled(Exception):
    """Raised internally when the caller cancels a transfer."""


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    path: str
    size: int = 0
    mtime: int = 0
    is_dir: bool = False
    is_symlink: bool = False
    mode: int = 0

    @property
    def permissions(self) -> str:
        if not self.mode:
            return "---------"
        return stat_module.filemode(self.mode)[1:]

    def as_row(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": "dir" if self.is_dir else ("link" if self.is_symlink else "file"),
            "size": self.size,
            "modified": self.mtime,
            "permissions": self.permissions,
        }


@dataclass
class TransferResult:
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


class SFTPSession(ParamikoSSHAdapter):
    """SSH connection with an SFTP subsystem opened on top of it."""

    protocol = "sftp"

    def __init__(self, known_hosts: object | None = None, **kwargs: object) -> None:
        super().__init__(known_hosts=known_hosts, **kwargs)  # type: ignore[arg-type]
        self._sftp: object | None = None

    # -- lifecycle ----------------------------------------------------------
    def connect(self, target: ConnectionTarget) -> ConnectResult:
        result = super().connect(target)
        client = super()._require_client()
        try:
            self._sftp = client.open_sftp()
        except Exception as exc:
            super().close()
            raise ProtocolError(
                f"the device accepted SSH but did not provide SFTP: {exc}",
                kind=ConnectionErrorKind.PROTOCOL,
                detail=str(exc),
            ) from exc
        return result

    def close(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.close()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                pass
            self._sftp = None
        super().close()

    def _require_sftp(self) -> object:
        if self._sftp is None:
            raise ProtocolError("the SFTP subsystem is not open", kind=ConnectionErrorKind.PROTOCOL)
        return self._sftp

    # -- browsing -----------------------------------------------------------
    def listdir(self, path: str = ".") -> list[RemoteEntry]:
        sftp = self._require_sftp()
        try:
            attributes = sftp.listdir_attr(path)  # type: ignore[attr-defined]
        except IOError as exc:
            raise _transfer_error(f"could not list {path}", exc) from exc
        entries: list[RemoteEntry] = []
        for attribute in attributes:
            mode = getattr(attribute, "st_mode", 0)
            entries.append(
                RemoteEntry(
                    name=attribute.filename,
                    path=_join(path, attribute.filename),
                    size=int(getattr(attribute, "st_size", 0) or 0),
                    mtime=int(getattr(attribute, "st_mtime", 0) or 0),
                    is_dir=stat_module.S_ISDIR(mode),
                    is_symlink=stat_module.S_ISLNK(mode),
                    mode=mode,
                )
            )
        entries.sort(key=lambda entry: (not entry.is_dir, entry.name.casefold()))
        return entries

    def stat(self, path: str) -> RemoteEntry:
        sftp = self._require_sftp()
        try:
            attribute = sftp.stat(path)  # type: ignore[attr-defined]
        except IOError as exc:
            raise _transfer_error(f"could not stat {path}", exc) from exc
        mode = getattr(attribute, "st_mode", 0)
        return RemoteEntry(
            name=os.path.basename(path.rstrip("/")) or path,
            path=path,
            size=int(getattr(attribute, "st_size", 0) or 0),
            mtime=int(getattr(attribute, "st_mtime", 0) or 0),
            is_dir=stat_module.S_ISDIR(mode),
            is_symlink=stat_module.S_ISLNK(mode),
            mode=mode,
        )

    def mkdir(self, path: str) -> None:
        try:
            self._require_sftp().mkdir(path)  # type: ignore[attr-defined]
        except IOError as exc:
            raise _transfer_error(f"could not create {path}", exc) from exc

    def remove(self, path: str) -> None:
        try:
            self._require_sftp().remove(path)  # type: ignore[attr-defined]
        except IOError as exc:
            raise _transfer_error(f"could not delete {path}", exc) from exc

    def rmdir(self, path: str) -> None:
        try:
            self._require_sftp().rmdir(path)  # type: ignore[attr-defined]
        except IOError as exc:
            raise _transfer_error(f"could not remove directory {path}", exc) from exc

    def rename(self, source: str, destination: str) -> None:
        try:
            self._require_sftp().rename(source, destination)  # type: ignore[attr-defined]
        except IOError as exc:
            raise _transfer_error(f"could not rename {source}", exc) from exc

    def chmod(self, path: str, mode: int) -> None:
        try:
            self._require_sftp().chmod(path, mode)  # type: ignore[attr-defined]
        except IOError as exc:
            raise _transfer_error(f"could not change permissions on {path}", exc) from exc

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
    ) -> TransferResult:
        local = Path(local_path)
        if not local.is_file():
            raise ProtocolError(f"{local} is not a file", kind=ConnectionErrorKind.UNKNOWN)
        total = local.stat().st_size
        sftp = self._require_sftp()
        offset = 0
        if resume:
            try:
                remote_size = int(sftp.stat(remote_path).st_size)  # type: ignore[attr-defined]
            except IOError:
                remote_size = 0
            if 0 < remote_size <= total:
                offset = remote_size
        result = TransferResult(
            direction="upload",
            local_path=str(local),
            remote_path=remote_path,
            total_bytes=total,
            resumed_from=offset,
            bytes_transferred=offset,
        )
        mode = "r+b" if offset else "wb"
        try:
            with open(local, "rb") as source:
                remote_file = sftp.open(remote_path, mode)  # type: ignore[attr-defined]
                with remote_file:
                    if offset:
                        source.seek(offset)
                        remote_file.seek(offset)
                    transferred = offset
                    while True:
                        if cancel is not None and cancel():
                            raise TransferCancelled()
                        block = source.read(chunk_size)
                        if not block:
                            break
                        remote_file.write(block)
                        transferred += len(block)
                        result.bytes_transferred = transferred
                        if progress is not None:
                            progress(transferred, total)
        except TransferCancelled:
            result.cancelled = True
            result.message = "transfer cancelled by the user"
            return result
        except IOError as exc:
            raise _transfer_error(f"upload to {remote_path} failed", exc) from exc
        result.message = f"uploaded {total} bytes to {remote_path}"
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
    ) -> TransferResult:
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        sftp = self._require_sftp()
        try:
            attribute = sftp.stat(remote_path)  # type: ignore[attr-defined]
        except IOError as exc:
            raise _transfer_error(f"could not stat {remote_path}", exc) from exc
        total = int(getattr(attribute, "st_size", 0) or 0)
        offset = 0
        if resume and local.exists():
            existing = local.stat().st_size
            if 0 < existing <= total:
                offset = existing
        result = TransferResult(
            direction="download",
            local_path=str(local),
            remote_path=remote_path,
            total_bytes=total,
            resumed_from=offset,
            bytes_transferred=offset,
        )
        mode = "r+b" if offset else "wb"
        try:
            with open(local, mode) as target:
                remote_file = sftp.open(remote_path, "rb")  # type: ignore[attr-defined]
                with remote_file:
                    if offset:
                        remote_file.seek(offset)
                        target.seek(offset)
                    transferred = offset
                    while True:
                        if cancel is not None and cancel():
                            raise TransferCancelled()
                        block = remote_file.read(chunk_size)
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
        except IOError as exc:
            raise _transfer_error(f"download of {remote_path} failed", exc) from exc
        result.message = f"downloaded {total} bytes from {remote_path}"
        return result


def _join(directory: str, name: str) -> str:
    if directory in ("", "."):
        return name
    return directory.rstrip("/") + "/" + name


def _transfer_error(message: str, exc: Exception) -> ProtocolError:
    text = str(exc)
    lowered = text.lower()
    if "permission denied" in lowered:
        kind = ConnectionErrorKind.AUTH
        hint = "the account lacks permission for this path"
    elif "no space" in lowered or "quota" in lowered:
        kind = ConnectionErrorKind.UNKNOWN
        hint = "the destination filesystem is full or over quota"
    elif "no such file" in lowered:
        kind = ConnectionErrorKind.UNKNOWN
        hint = "the path does not exist"
    elif "broken pipe" in lowered or "connection" in lowered:
        kind = ConnectionErrorKind.PROTOCOL
        hint = "the connection was interrupted"
    else:
        kind = ConnectionErrorKind.UNKNOWN
        hint = ""
    detail = f"{text}" + (f" ({hint})" if hint else "")
    return ProtocolError(f"{message}: {text}", kind=kind, detail=detail)
