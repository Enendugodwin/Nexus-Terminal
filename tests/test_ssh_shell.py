"""End-to-end SSH shell test against an in-process paramiko server.

This exercises the real transport path: host-key refusal, explicit trust,
password authentication, ``invoke_shell`` PTY allocation, echo and PTY resize.
No external host is contacted.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time

import paramiko
import pytest

from nexus_remote.connections.interfaces import ConnectionErrorKind, ConnectionTarget
from nexus_remote.protocols.ssh import ParamikoSSHAdapter
from nexus_remote.security.host_keys import KnownHosts

USERNAME = "deploy"
PASSWORD = "test-secret-value"


class _Server(paramiko.ServerInterface):
    def __init__(self) -> None:
        self.shell_requested = threading.Event()
        self.term_size: tuple[int, int] | None = None

    def check_auth_password(self, username: str, password: str) -> int:
        if username == USERNAME and password == PASSWORD:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes) -> bool:
        self.term_size = (width, height)
        return True

    def check_channel_shell_request(self, channel) -> bool:
        self.shell_requested.set()
        return True


class _SSHServer:
    """Tiny echo server that accepts several sequential test connections."""

    def __init__(self) -> None:
        self.host_key = paramiko.RSAKey.generate(2048)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(4)
        self.port = self.socket.getsockname()[1]
        self.interface: _Server | None = None
        self.error: BaseException | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> _SSHServer:
        self._thread.start()
        return self

    def _serve(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    connection, _address = self.socket.accept()
                except OSError:
                    break
                self._handle(connection)
        except BaseException as exc:
            self.error = exc

    def _handle(self, connection: socket.socket) -> None:
        transport = paramiko.Transport(connection)
        interface = _Server()
        try:
            transport.add_server_key(self.host_key)
            transport.start_server(server=interface)
            channel = transport.accept(5)
            if channel is None:
                return
            interface.shell_requested.wait(10)
            self.interface = interface
            channel.sendall(b"\x1b[32mready\x1b[0m\r\n$ ")
            deadline = time.time() + 20
            while time.time() < deadline and not channel.closed:
                if channel.recv_ready():
                    data = channel.recv(4096)
                    if not data:
                        break
                    channel.sendall(data)  # echo everything back
                if channel.exit_status_ready():
                    break
                time.sleep(0.01)
        finally:
            transport.close()

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self.socket.close()
        self._thread.join(timeout=8)


def _read_until(shell, needle: bytes, timeout: float = 5.0) -> bytes:
    collected = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = shell.read(timeout=0.1)
        if chunk:
            collected += chunk
            if needle in collected:
                break
    return collected


def test_unknown_host_key_is_refused_then_trusted_and_shell_works(database, tmp_path, monkeypatch):
    # Never let a real ~/.ssh/known_hosts influence the result.
    monkeypatch.setattr(paramiko.SSHClient, "load_system_host_keys", lambda self, filename=None: None)

    known_hosts = KnownHosts(database, tmp_path / "known_hosts")
    server = _SSHServer().start()
    target = ConnectionTarget(
        host="127.0.0.1",
        protocol="ssh",
        port=server.port,
        username=USERNAME,
        secret=PASSWORD,
    )
    adapter = ParamikoSSHAdapter(known_hosts=known_hosts, look_for_keys=False, allow_agent=False)
    try:
        with pytest.raises(Exception) as excinfo:
            adapter.connect(target)
        error = excinfo.value
        assert getattr(error, "kind", None) is ConnectionErrorKind.HOST_KEY

        pending = known_hosts.pending()
        assert pending
        # paramiko reports the identity as "[host]:port" for non-default ports.
        assert known_hosts.trust_pending(pending[0]["host"], pending[0]["key_type"])

        result = adapter.connect(target)
        assert result.ok
        assert result.host_key_fingerprint.startswith("SHA256:")

        shell = adapter.open_shell((100, 30))
        banner = _read_until(shell, b"ready")
        assert b"ready" in banner
        # The PTY size reached the server.
        assert server.interface.term_size == (100, 30)

        shell.write("whoami\r")
        echoed = _read_until(shell, b"whoami")
        assert b"whoami" in echoed

        # Resizing must not raise and must not break the channel.
        shell.resize(120, 40)
        shell.write("x")
        assert b"x" in _read_until(shell, b"x", timeout=3.0)
        shell.close()
    finally:
        adapter.close()
        server.stop()
    assert server.error is None


def test_wrong_password_is_reported_as_an_auth_error(database, tmp_path, monkeypatch):
    monkeypatch.setattr(paramiko.SSHClient, "load_system_host_keys", lambda self, filename=None: None)
    known_hosts = KnownHosts(database, tmp_path / "known_hosts")
    server = _SSHServer().start()
    known_hosts.trust(f"[127.0.0.1]:{server.port}", server.host_key.get_name(), server.host_key)
    adapter = ParamikoSSHAdapter(known_hosts=known_hosts, look_for_keys=False, allow_agent=False)
    target = ConnectionTarget(
        host="127.0.0.1",
        protocol="ssh",
        port=server.port,
        username=USERNAME,
        secret="wrong-password",
    )
    try:
        with pytest.raises(Exception) as excinfo:
            adapter.connect(target)
        assert getattr(excinfo.value, "kind", None) is ConnectionErrorKind.AUTH
    finally:
        adapter.close()
        server.stop()
