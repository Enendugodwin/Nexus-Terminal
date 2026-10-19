"""Forcible session termination: stuck sessions must never wedge the UI."""

from __future__ import annotations

import threading
import time

from nexus_remote.connections.interfaces import (
    CommandResult,
    ConnectResult,
    ProtocolAdapter,
    SessionState,
)


class QuickAdapter(ProtocolAdapter):
    protocol = "ssh"

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def connect(self, target):
        self._connected = True
        return ConnectResult(ok=True, message="ok", auth_method="password")

    def close(self) -> None:
        self._connected = False
        self.closed = True

    def execute(self, command: str, timeout: float = 30.0) -> CommandResult:
        return CommandResult(command=command, exit_code=0)


class WedgedAdapter(QuickAdapter):
    """A session whose adapter refuses to close (a hung transport)."""

    def __init__(self) -> None:
        super().__init__()
        self.close_entered = threading.Event()

    def close(self) -> None:
        self.close_entered.set()
        threading.Event().wait(30)  # never returns on its own


def _connect(manager, repository, credentials, device, adapter):
    repository.add(device)
    credentials.save(device.id, "password", "deploy", "secret-value")
    manager.register_adapter("ssh", lambda: adapter)
    return manager.connect(manager.open(device, "ssh"))


def test_force_close_abandons_a_wedged_adapter(manager, repository, credentials, device):
    adapter = WedgedAdapter()
    session = _connect(manager, repository, credentials, device, adapter)
    assert session.state is SessionState.CONNECTED

    started = time.monotonic()
    manager.force_close(session, timeout=0.1)
    elapsed = time.monotonic() - started

    assert session.state is SessionState.CLOSED
    assert session.closed_at
    assert session.adapter is None
    assert elapsed < 1.0, "force_close blocked on a wedged adapter"


def test_force_close_works_on_a_session_that_never_connected(manager, device):
    session = manager._sessions.get("missing")
    assert session is None
    # A pending session (no adapter) is still closeable.
    from nexus_remote.connections.session import Session

    pending = Session(device_id=device.id, device_name=device.name, protocol="ssh", host=device.hostname)
    manager._sessions[pending.id] = pending
    manager.force_close(pending, timeout=0.1)
    assert pending.state is SessionState.CLOSED


def test_terminate_all_closes_every_open_session(manager, repository, credentials, device):
    repository.add(device)
    credentials.save(device.id, "password", "deploy", "secret-value")
    manager.register_adapter("ssh", QuickAdapter)
    first = manager.connect(manager.open(device, "ssh"))
    second = manager.connect(manager.open(device, "ssh"))

    assert manager.terminate_all(timeout=0.1) == 2
    assert first.state is SessionState.CLOSED
    assert second.state is SessionState.CLOSED
    assert manager.count_active() == 0
    # Nothing left to kill.
    assert manager.terminate_all(timeout=0.1) == 0
