"""RDP is driven by FreeRDP and embedded in the app (no ``mstsc``)."""

from __future__ import annotations

from nexus_remote.connections.interfaces import (
    ConnectionErrorKind,
    ConnectionTarget,
    ProtocolError,
)
from nexus_remote.protocols import rdp
from nexus_remote.protocols.rdp import RDPAdapter, RDPOptions, freerdp_candidates


class RecordingStdin:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    def write(self, payload: bytes) -> int:
        self.data += payload
        return len(payload)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = RecordingStdin()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None):
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeLauncher:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        return self.process


def _target(**options) -> ConnectionTarget:
    return ConnectionTarget(
        host="10.0.0.9",
        protocol="rdp",
        port=3389,
        username="administrator",
        secret="s3cr3t",
        options=options,
    )


def test_client_is_freerdp_not_mstsc():
    # The discovery list must never prefer the native Windows client.
    assert all("mstsc" not in name.lower() for name in rdp._CLIENT_NAMES)
    for candidate in freerdp_candidates():
        assert "mstsc" not in candidate.lower()


def test_per_user_tools_directory_is_searched(monkeypatch, tmp_path):
    managed = tmp_path / "freerdp"
    managed.mkdir()
    client = managed / "sdl-freerdp.exe"
    client.write_bytes(b"MZ")
    monkeypatch.setattr(rdp, "managed_client_dir", lambda: managed)
    monkeypatch.setattr(rdp, "search_dirs", lambda: [managed])
    candidates = freerdp_candidates()
    assert str(client) in candidates
    assert rdp.freerdp_client_path() == str(client)


def test_password_goes_to_stdin_and_never_to_argv(monkeypatch):
    process = FakeProcess()
    launcher = FakeLauncher(process)
    adapter = RDPAdapter(launcher=launcher, client_path="wlfreerdp.exe")
    result = adapter.connect(_target())
    command = launcher.commands[0]
    assert command[0] == "wlfreerdp.exe"
    assert "/v:10.0.0.9" in command
    assert "/u:administrator" in command
    assert "/from-stdin" in command
    assert "s3cr3t" not in " ".join(command)
    assert process.stdin.data == b"s3cr3t\n"
    assert process.stdin.closed
    assert result.ok and result.metadata["embedded"] is False


def test_embedded_session_passes_the_parent_window_handle():
    launcher = FakeLauncher(FakeProcess())
    adapter = RDPAdapter(launcher=launcher, client_path="wlfreerdp.exe")
    result = adapter.connect(_target(parent_window=987654))
    command = launcher.commands[0]
    # The SDL client ignores /parent-window; the window is re-parented by Win32.
    assert not any(option.startswith("/parent-window:") for option in command)
    assert result.metadata["embedded"] is True
    assert result.metadata["parent_window"] == 987654


def test_embedding_helper_is_inert_without_a_window():
    assert rdp.embed_client_window(0, 0, timeout=0.1) == 0


def test_exited_reflects_the_client_process():
    process = FakeProcess()
    adapter = RDPAdapter(launcher=FakeLauncher(process), client_path="wlfreerdp.exe")
    adapter.connect(_target())
    assert adapter.exited is False
    process.returncode = 1
    assert adapter.exited is True


class FakeStderr:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class ErrorProcess(FakeProcess):
    def __init__(self, lines: list[bytes]) -> None:
        super().__init__()
        self.stderr = FakeStderr(lines)


def test_fatal_client_error_is_explained_and_stops_the_client():
    process = ErrorProcess(
        [b"[ERROR][com.freerdp.core] - [nla_recv_pdu]: ERRCONNECT_AUTHENTICATION_FAILED [0x00020009]\n"]
    )
    adapter = RDPAdapter(launcher=FakeLauncher(process), client_path="wlfreerdp.exe")
    adapter.connect(_target())
    adapter._reader_thread.join(2.0)
    assert adapter.last_error.startswith("authentication failed")
    assert process.terminated
    assert "ERRCONNECT_AUTHENTICATION_FAILED" in adapter.diagnostics


def test_common_fatal_errors_have_plain_language_messages():
    assert "NLA" in rdp._fatal_error("ERRCONNECT_HYBRID_REQUIRED_BY_SERVER")
    assert "resolved" in rdp._fatal_error("ERRCONNECT_DNS_ERROR")
    assert rdp._fatal_error("[WARN] nothing to see") == ""


def test_missing_client_raises_a_policy_error(monkeypatch):
    monkeypatch.setattr(rdp, "freerdp_client_path", lambda: None)
    adapter = RDPAdapter(client_path=None)
    try:
        adapter.connect(_target())
    except ProtocolError as exc:
        assert exc.kind is ConnectionErrorKind.POLICY
        assert "FreeRDP" in str(exc)
        assert "mstsc" in exc.detail
    else:  # pragma: no cover - the call must fail
        raise AssertionError("connecting without FreeRDP should raise")


def test_close_terminates_the_client_process():
    process = FakeProcess()
    adapter = RDPAdapter(launcher=FakeLauncher(process), client_path="wlfreerdp.exe")
    adapter.connect(_target())
    adapter.close()
    assert process.terminated
    assert adapter.connected is False


def test_certificate_check_is_on_unless_opted_out():
    adapter = RDPAdapter(client_path="wlfreerdp.exe")
    target = _target()
    trusted_on_first_use = adapter.build_command(target, RDPOptions(client="wlfreerdp.exe"))
    assert "/cert:ignore" not in trusted_on_first_use
    assert "/cert:tofu" in trusted_on_first_use
    opted_out = adapter.build_command(target, RDPOptions(cert_ignore=True, client="wlfreerdp.exe"))
    assert "/cert:ignore" in opted_out
    assert "/cert:tofu" not in opted_out


def test_non_default_port_is_included_in_the_address():
    adapter = RDPAdapter(client_path="wlfreerdp.exe")
    target = ConnectionTarget(host="rdp.example", protocol="rdp", port=3390)
    command = adapter.build_command(target, RDPOptions(client="wlfreerdp.exe"))
    assert "/v:rdp.example:3390" in command
