"""SMB adapter and file-manager wiring, against a fake ``smbclient`` module."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from nexus_remote.connections.interfaces import ConnectionErrorKind, ConnectionTarget, ProtocolError
from nexus_remote.devices.models import Device, ProtocolConfig
from nexus_remote.protocols.smb import SMBAdapter, SMBEntry, _smb_error


# -- fake smbclient ---------------------------------------------------------
class _FakeStat:
    def __init__(self, *, is_dir: bool, size: int, mtime: float = 0.0) -> None:
        self.st_is_dir = is_dir
        self.st_size = size
        self.st_mtime = mtime


class _FakeOpenFile:
    def __init__(self, client: "FakeSMBClient", path: str, mode: str) -> None:
        self.client = client
        self.path = path
        self.mode = mode
        self.buffer = bytearray(client.files.get(path, b"") if ("r" in mode or "+" in mode or "a" in mode) else b"")
        self.pos = 0

    def __enter__(self) -> "_FakeOpenFile":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if "w" in self.mode or "+" in self.mode or "a" in self.mode:
            self.client.files[self.path] = bytes(self.buffer)

    def read(self, size: int) -> bytes:
        chunk = bytes(self.buffer[self.pos : self.pos + size])
        self.pos += len(chunk)
        return chunk

    def write(self, data: bytes) -> int:
        end = self.pos + len(data)
        if end > len(self.buffer):
            self.buffer.extend(b"\x00" * (end - len(self.buffer)))
        self.buffer[self.pos : end] = data
        self.pos = end
        return len(data)

    def seek(self, offset: int) -> None:
        self.pos = offset


class FakeSMBClient:
    """Minimal in-memory stand-in for the ``smbclient`` module."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.registered: list[dict[str, object]] = []
        self.deleted_sessions: list[tuple[str, int]] = []
        self.renames: list[tuple[str, str]] = []

    # -- session ---------------------------------------------------------
    def register_session(self, host: str, **kwargs: object) -> None:
        if host == "denied-host":
            raise Exception("STATUS_LOGON_FAILURE: the username or password is incorrect")
        self.registered.append({"host": host, **kwargs})

    def delete_session(self, host: str, port: int = 445) -> None:
        self.deleted_sessions.append((host, port))

    # -- metadata --------------------------------------------------------
    @staticmethod
    def _key(path: str) -> str:
        """Map a UNC path (``\\\\host\\share\\dir``) to an in-memory key."""
        text = path.rstrip("\\")
        if text.startswith("\\\\"):
            parts = text.lstrip("\\").split("\\", 1)
            return parts[1] if len(parts) > 1 else ""
        return text

    def listdir(self, path: str) -> list[str]:
        key = self._key(path)
        prefix = f"{key}\\" if key else ""
        names = set()
        for known in list(self.files) + list(self.dirs):
            if not known.startswith(prefix):
                continue
            remainder = known[len(prefix) :]
            if remainder:
                names.add(remainder.split("\\", 1)[0])
        return sorted(names)

    def stat(self, path: str) -> _FakeStat:
        key = self._key(path)
        if key in self.dirs:
            return _FakeStat(is_dir=True, size=0, mtime=0.0)
        if key in self.files:
            return _FakeStat(is_dir=False, size=len(self.files[key]), mtime=1.0)
        raise FileNotFoundError(key)

    def open_file(self, path: str, mode: str = "rb", **_: object) -> _FakeOpenFile:
        key = self._key(path)
        if "r" in mode and "+" not in mode and key not in self.files:
            raise FileNotFoundError(key)
        return _FakeOpenFile(self, key, mode)

    def mkdir(self, path: str) -> None:
        self.dirs.add(self._key(path))

    def remove(self, path: str) -> None:
        key = self._key(path)
        if key not in self.files:
            raise FileNotFoundError(key)
        del self.files[key]

    def rmdir(self, path: str) -> None:
        self.dirs.discard(self._key(path))

    def rename(self, source: str, destination: str) -> None:
        self.renames.append((source, destination))


@pytest.fixture()
def fake_smb(monkeypatch):
    client = FakeSMBClient()
    monkeypatch.setitem(sys.modules, "smbclient", client)
    return client


def _target(port: int = 445, **options: object) -> ConnectionTarget:
    return ConnectionTarget(
        host="fileserver",
        protocol="smb",
        port=port,
        username="operator",
        secret="secret",
        options=dict(options),
    )


def _connected(fake_smb: FakeSMBClient, share: str = "Data") -> SMBAdapter:
    fake_smb.dirs.add("Data")
    adapter = SMBAdapter()
    adapter.connect(_target(share=share))
    return adapter


# -- connection -------------------------------------------------------------
def test_connect_requires_the_optional_package(monkeypatch):
    import nexus_remote.protocols.smb as smb_module

    monkeypatch.setattr(smb_module, "smb_available", lambda: False)
    with pytest.raises(ProtocolError) as excinfo:
        SMBAdapter().connect(_target(share="Data"))
    assert excinfo.value.kind is ConnectionErrorKind.POLICY
    assert "smbprotocol" in str(excinfo.value)


def test_connect_requires_a_share(fake_smb):
    with pytest.raises(ProtocolError) as excinfo:
        SMBAdapter().connect(_target())
    assert excinfo.value.kind is ConnectionErrorKind.POLICY
    assert "share" in str(excinfo.value).lower()


def test_connect_registers_a_secure_session(fake_smb):
    adapter = SMBAdapter()
    result = adapter.connect(_target(share=" Data ", port=1445))

    assert result.ok is True
    assert result.metadata["share"] == "Data"
    registration = fake_smb.registered[0]
    assert registration["username"] == "operator"
    assert registration["port"] == 1445
    assert registration["encrypt"] is True
    assert registration["require_secure_negotiate"] is True

    adapter.close()
    assert fake_smb.deleted_sessions == [("fileserver", 1445)]
    assert adapter.connected is False


def test_authentication_error_is_classified(fake_smb):
    with pytest.raises(ProtocolError) as excinfo:
        SMBAdapter().connect(
            ConnectionTarget(host="denied-host", protocol="smb", username="x", secret="y", options={"share": "Data"})
        )
    assert excinfo.value.kind is ConnectionErrorKind.AUTH


# -- browsing ---------------------------------------------------------------
def test_unc_paths_are_built_from_share_relative_paths(fake_smb):
    adapter = _connected(fake_smb)

    assert adapter._unc("") == "\\\\fileserver\\Data"
    assert adapter._unc("/") == "\\\\fileserver\\Data"
    assert adapter._unc(".") == "\\\\fileserver\\Data"
    assert adapter._unc("logs\\app.log") == "\\\\fileserver\\Data\\logs\\app.log"
    assert adapter._unc("/logs/app.log") == "\\\\fileserver\\Data\\logs\\app.log"


def test_listdir_returns_share_relative_paths_and_dirs_first(fake_smb):
    adapter = _connected(fake_smb)
    fake_smb.dirs.update({"Data\\zdir", "Data\\adir"})
    fake_smb.files["Data\\b.txt"] = b"x" * 10

    entries = adapter.listdir("")

    assert [entry.name for entry in entries] == ["adir", "zdir", "b.txt"]
    assert entries[0].path == "adir"
    assert entries[0].is_dir is True
    assert entries[2].size == 10
    assert entries[2].permissions == ""

    nested = adapter.listdir("adir")
    assert nested == []


def test_browsing_before_connect_is_refused(fake_smb):
    with pytest.raises(ProtocolError) as excinfo:
        SMBAdapter().listdir("")
    assert excinfo.value.kind is ConnectionErrorKind.PROTOCOL


def test_mutating_helpers_target_the_share(fake_smb):
    adapter = _connected(fake_smb)

    adapter.mkdir("newdir")
    assert "Data\\newdir" in fake_smb.dirs

    fake_smb.files["Data\\old.txt"] = b"data"
    adapter.rename("old.txt", "new.txt")
    assert fake_smb.renames == [("\\\\fileserver\\Data\\old.txt", "\\\\fileserver\\Data\\new.txt")]

    adapter.remove("old.txt")
    assert "Data\\old.txt" not in fake_smb.files

    adapter.rmdir("newdir")
    assert "Data\\newdir" not in fake_smb.dirs


def test_missing_file_reports_a_protocol_error(fake_smb):
    adapter = _connected(fake_smb)
    with pytest.raises(ProtocolError):
        adapter.remove("ghost.txt")


# -- transfers --------------------------------------------------------------
def test_upload_streams_and_reports_progress(fake_smb, tmp_path):
    adapter = _connected(fake_smb)
    source = tmp_path / "payload.bin"
    source.write_bytes(b"a" * (3 * 1024))

    seen: list[tuple[int, int]] = []
    result = adapter.upload(source, "inbox/payload.bin", progress=lambda done, total: seen.append((done, total)), chunk_size=1024)

    assert fake_smb.files["Data\\inbox\\payload.bin"] == source.read_bytes()
    assert result.bytes_transferred == 3 * 1024
    assert result.total_bytes == 3 * 1024
    assert seen[-1] == (3 * 1024, 3 * 1024)
    assert "inbox/payload.bin" in result.message


def test_upload_resumes_from_the_existing_remote_size(fake_smb, tmp_path):
    adapter = _connected(fake_smb)
    source = tmp_path / "payload.bin"
    source.write_bytes(b"0123456789")
    fake_smb.files["Data\\payload.bin"] = b"01234"

    result = adapter.upload(source, "payload.bin", chunk_size=2)

    assert result.resumed_from == 5
    assert fake_smb.files["Data\\payload.bin"] == b"0123456789"


def test_upload_can_be_cancelled(fake_smb, tmp_path):
    adapter = _connected(fake_smb)
    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * 4096)

    result = adapter.upload(source, "big.bin", cancel=lambda: True, chunk_size=512)

    assert result.cancelled is True
    assert result.bytes_transferred == 0
    assert "cancelled" in result.message


def test_download_writes_the_local_file_and_can_resume(fake_smb, tmp_path):
    adapter = _connected(fake_smb)
    fake_smb.files["Data\\logs\\app.log"] = b"0123456789"
    target = tmp_path / "downloads" / "app.log"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"0123")

    result = adapter.download("logs/app.log", target, chunk_size=4)

    assert result.resumed_from == 4
    assert result.total_bytes == 10
    assert target.read_bytes() == b"0123456789"
    assert "downloaded 10 bytes" in result.message


def test_download_of_a_missing_file_is_reported(fake_smb, tmp_path):
    adapter = _connected(fake_smb)
    with pytest.raises(ProtocolError):
        adapter.download("ghost.txt", tmp_path / "ghost.txt")


# -- file manager wiring (shared by SFTP and SMB) ---------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture()
def smb_device():
    return Device(
        name="fileserver",
        hostname="10.0.0.30",
        operating_system="windows",
        environment="lab",
        connections={
            "smb": ProtocolConfig("smb", enabled=True, port=445, username="operator", extra={"share": "Data"}),
        },
    ).validate()


def _connected_session(services, device, adapter):
    services.devices.add(device)
    session = services.sessions.open(device, "smb")
    session.transition(session.state.CONNECTING)
    session.transition(session.state.CONNECTED)
    session._adapter = adapter
    return session


def test_files_tab_lists_smb_sessions(qapp, services, smb_device):
    from nexus_remote.ui.workspace import WorkspacePage

    class ListingAdapter:
        def close(self):
            return None

        def listdir(self, path=""):
            return [SMBEntry(name="share-root", path="share-root", is_dir=True)]

    session = _connected_session(services, smb_device, ListingAdapter())
    workspace = WorkspacePage(services)
    workspace._refresh_remote_session_combo()

    assert workspace.file_session_combo.count() == 1
    assert "SMB" in workspace.file_session_combo.itemText(0)
    assert workspace.file_session_combo.currentData() == session.id
    assert workspace.upload_button.isEnabled()


def test_transfer_buttons_start_disabled(qapp, services):
    from nexus_remote.ui.workspace import WorkspacePage

    workspace = WorkspacePage(services)

    assert workspace.file_session_combo.count() == 0
    assert workspace.upload_button.isEnabled() is False
    assert workspace.download_button.isEnabled() is False
    assert workspace.delete_remote_button.isEnabled() is False


def test_files_tab_renders_smb_entries_with_sizes(qapp, services, smb_device):
    from nexus_remote.ui.workspace import WorkspacePage

    class ListingAdapter:
        def close(self):
            return None

        def listdir(self, path=""):
            return [
                SMBEntry(name="inbox", path="inbox", is_dir=True),
                SMBEntry(name="report.pdf", path="report.pdf", size=1536),
            ]

    session = _connected_session(services, smb_device, ListingAdapter())
    workspace = WorkspacePage(services)
    workspace._refresh_remote_session_combo()
    workspace.file_session_combo.setCurrentIndex(0)

    assert workspace.remote_path == ""  # the share root, not "."
    assert workspace.remote_list.count() == 2
    assert workspace.remote_list.item(0).text().startswith("[ ] inbox")
    assert "1.5 KB" in workspace.remote_list.item(1).text()
    assert "SMB" in workspace.transfer_status.text()
    assert session.id == workspace.file_session_combo.currentData()


def test_smb_navigation_moves_up_to_the_share_root(qapp, services, smb_device):
    from nexus_remote.ui.workspace import WorkspacePage

    class ListingAdapter:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def close(self):
            return None

        def listdir(self, path=""):
            self.paths.append(path)
            return [SMBEntry(name="nested", path="logs/nested", is_dir=True)] if path == "logs" else []

    adapter = ListingAdapter()
    _connected_session(services, smb_device, adapter)
    workspace = WorkspacePage(services)
    workspace._refresh_remote_session_combo()
    workspace.file_session_combo.setCurrentIndex(0)

    workspace.load_remote("logs")
    workspace._remote_up()
    workspace._remote_up()  # already at the root: no further navigation

    assert adapter.paths == ["", "logs", ""]
    assert workspace.remote_path == ""
    assert workspace.remote_path_edit.text() == ""


def test_smb_errors_are_classified():
    assert _smb_error("x", Exception("STATUS_ACCESS_DENIED")).kind is ConnectionErrorKind.AUTH
    assert _smb_error("x", Exception("signing is required")).kind is ConnectionErrorKind.POLICY
    assert _smb_error("x", Exception("connection timed out")).kind is ConnectionErrorKind.TIMEOUT
    assert _smb_error("x", Exception("weird")).kind is ConnectionErrorKind.UNKNOWN


# -- device profile editor --------------------------------------------------
def test_device_dialog_stores_the_share(qapp, services):
    from nexus_remote.ui.device_dialog import DeviceDialog

    dialog = DeviceDialog(services)
    dialog.name_edit.setText("fileserver")
    dialog.host_edit.setText("fileserver01")
    enabled, _port, _auth, username, share = dialog._protocol_widgets["smb"]
    enabled.setChecked(True)
    share.setText(" Data ")
    username.setText("operator")

    device = dialog._build_device()

    assert device.connections["smb"].enabled is True
    assert device.connections["smb"].extra["share"] == "Data"
    assert device.connections["smb"].username == "operator"


def test_device_dialog_prefills_and_clears_the_share(qapp, services, smb_device):
    from nexus_remote.ui.device_dialog import DeviceDialog

    dialog = DeviceDialog(services, smb_device)
    _enabled, _port, _auth, _username, share = dialog._protocol_widgets["smb"]
    assert share.text() == "Data"

    share.setText("")
    device = dialog._build_device()
    assert "share" not in device.connections["smb"].extra


def test_device_dialog_keeps_other_extra_options(qapp, services):
    from nexus_remote.devices.models import Device, ProtocolConfig
    from nexus_remote.ui.device_dialog import DeviceDialog

    device = Device(
        name="nas",
        hostname="nas01",
        connections={
            "smb": ProtocolConfig("smb", enabled=True, extra={"share": "Data", "encrypt": False}),
        },
    ).validate()
    dialog = DeviceDialog(services, device)
    rebuilt = dialog._build_device()

    assert rebuilt.connections["smb"].extra == {"share": "Data", "encrypt": False}


def test_sftp_navigation_keeps_the_dot_root(qapp, services):
    """The pane is shared: SFTP uses "." as its root, SMB the share root."""
    from nexus_remote.connections.interfaces import SessionState
    from nexus_remote.devices.models import Device, ProtocolConfig
    from nexus_remote.ui.workspace import WorkspacePage

    class ListingAdapter:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def close(self):
            return None

        def listdir(self, path="."):
            self.paths.append(path)
            return []

    device = Device(
        name="linux-01",
        hostname="10.0.0.40",
        connections={"sftp": ProtocolConfig("sftp", enabled=True, username="deploy")},
    ).validate()
    services.devices.add(device)
    adapter = ListingAdapter()
    session = services.sessions.open(device, "sftp")
    session.state = SessionState.CONNECTED
    session._adapter = adapter

    workspace = WorkspacePage(services)
    workspace._refresh_remote_session_combo()
    workspace.file_session_combo.setCurrentIndex(0)
    workspace.load_remote("etc")
    workspace._remote_up()
    workspace._remote_up()
    workspace.load_remote("/var/log")
    workspace._remote_up()

    assert adapter.paths == [".", "etc", ".", "/var/log", "/var"]
    assert workspace.remote_path == "/var"
