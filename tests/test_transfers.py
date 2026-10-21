"""SFTP transfer behaviour using an in-memory fake SFTP server."""

from __future__ import annotations

import io
import stat
from types import SimpleNamespace

import pytest

from nexus_remote.connections.interfaces import ProtocolError
from nexus_remote.protocols.sftp import SFTPSession


class FakeSFTPFile(io.BytesIO):
    def __init__(self, store: dict[str, bytes], path: str, data: bytes) -> None:
        super().__init__(data)
        self.store = store
        self.path = path
        self.written = 0

    def write(self, block) -> int:  # type: ignore[override]
        written = super().write(block)
        self.written += written
        return written

    def close(self) -> None:
        if not self.closed:
            self.store[self.path] = self.getvalue()
        super().close()

    def __exit__(self, *exc) -> None:  # ensure data is flushed on context exit
        self.close()


class FakeSFTP:
    """Minimal stand-in for paramiko.SFTPClient."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self.calls: list[tuple[str, tuple]] = []

    def open(self, path: str, mode: str) -> FakeSFTPFile:
        self.calls.append(("open", (path, mode)))
        data = b"" if mode.startswith("w") else self.files.get(path, b"")
        return FakeSFTPFile(self.files, path, data)

    def stat(self, path: str):
        self.calls.append(("stat", (path,)))
        if path not in self.files:
            raise OSError(2, "No such file")
        return SimpleNamespace(st_size=len(self.files[path]), st_mode=stat.S_IFREG | 0o644, st_mtime=1_700_000_000)

    def listdir_attr(self, path: str):
        self.calls.append(("listdir_attr", (path,)))
        entries = []
        for name, data in (("logs", b""), ("notes.txt", self.files.get("notes.txt", b"x"))):
            is_dir = name == "logs"
            entries.append(
                SimpleNamespace(
                    filename=name,
                    st_size=len(data),
                    st_mtime=1_700_000_000,
                    st_mode=(stat.S_IFDIR | 0o755) if is_dir else (stat.S_IFREG | 0o644),
                )
            )
        return entries

    def mkdir(self, path: str) -> None:
        self.calls.append(("mkdir", (path,)))

    def remove(self, path: str) -> None:
        self.calls.append(("remove", (path,)))
        self.files.pop(path, None)

    def rmdir(self, path: str) -> None:
        self.calls.append(("rmdir", (path,)))

    def rename(self, source: str, destination: str) -> None:
        self.calls.append(("rename", (source, destination)))

    def chmod(self, path: str, mode: int) -> None:
        self.calls.append(("chmod", (path, mode)))

    def close(self) -> None:
        self.calls.append(("close", ()))


def make_session(files: dict[str, bytes] | None = None) -> tuple[SFTPSession, FakeSFTP]:
    fake = FakeSFTP(files or {})
    session = SFTPSession()
    session._sftp = fake
    session._connected = True
    return session, fake


def test_listdir_reports_types_and_permissions():
    session, _ = make_session({"notes.txt": b"hello"})
    entries = session.listdir(".")
    names = [entry.name for entry in entries]
    assert names == ["logs", "notes.txt"]  # directories first
    logs = entries[0]
    assert logs.is_dir is True
    assert logs.path == "logs"
    notes = entries[1]
    assert notes.size == 5
    assert notes.permissions.startswith("rw")


def test_upload_streams_in_chunks(tmp_path):
    local = tmp_path / "payload.bin"
    payload = bytes(range(256)) * 40  # 10 KB
    local.write_bytes(payload)
    session, fake = make_session()
    progress: list[tuple[int, int]] = []
    result = session.upload(local, "/remote/payload.bin", progress=lambda done, total: progress.append((done, total)), chunk_size=1024)
    assert fake.files["/remote/payload.bin"] == payload
    assert result.bytes_transferred == len(payload)
    assert result.total_bytes == len(payload)
    assert result.resumed_from == 0
    assert progress and progress[-1][0] == len(payload)


def test_upload_resumes_from_existing_remote_size(tmp_path):
    local = tmp_path / "resume.bin"
    payload = b"0123456789"
    local.write_bytes(payload)
    session, fake = make_session({"/remote/resume.bin": b"0123"})
    result = session.upload(local, "/remote/resume.bin", chunk_size=4)
    assert result.resumed_from == 4
    assert fake.files["/remote/resume.bin"] == payload


def test_download_writes_local_file(tmp_path):
    session, _ = make_session({"/remote/data.txt": b"hello world"})
    target = tmp_path / "nested" / "data.txt"
    result = session.download("/remote/data.txt", target)
    assert target.read_bytes() == b"hello world"
    assert result.bytes_transferred == 11


def test_download_resumes_local_partial(tmp_path):
    session, _ = make_session({"/remote/data.txt": b"hello world"})
    target = tmp_path / "data.txt"
    target.write_bytes(b"hello ")
    result = session.download("/remote/data.txt", target)
    assert result.resumed_from == 6
    assert target.read_bytes() == b"hello world"


def test_upload_cancellation_returns_flag(tmp_path):
    local = tmp_path / "big.bin"
    local.write_bytes(b"x" * 4096)
    session, fake = make_session()
    calls = {"count": 0}

    def cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 1

    result = session.upload(local, "/remote/big.bin", cancel=cancel, chunk_size=512)
    assert result.cancelled is True
    assert result.bytes_transferred < 4096


def test_missing_local_file_raises(tmp_path):
    session, _ = make_session()
    with pytest.raises(ProtocolError):
        session.upload(tmp_path / "nope.bin", "/remote/nope.bin")


def test_download_missing_remote_file_raises(tmp_path):
    session, _ = make_session()
    with pytest.raises(ProtocolError):
        session.download("/remote/missing", tmp_path / "out.bin")


def test_browsing_helpers_are_forwarded():
    session, fake = make_session()
    session.mkdir("/remote/new")
    session.rename("/a", "/b")
    session.chmod("/b", 0o600)
    session.remove("/b")
    session.rmdir("/dir")
    calls = [name for name, _ in fake.calls]
    assert calls == ["mkdir", "rename", "chmod", "remove", "rmdir"]


def test_transfer_result_percent():
    from nexus_remote.protocols.sftp import TransferResult

    result = TransferResult(direction="upload", local_path="a", remote_path="b", bytes_transferred=5, total_bytes=10)
    assert result.percent == 50.0
    empty = TransferResult(direction="upload", local_path="a", remote_path="b")
    assert empty.percent == 0.0


def test_transfer_requires_open_subsystem():
    session = SFTPSession()
    with pytest.raises(ProtocolError):
        session.listdir(".")
