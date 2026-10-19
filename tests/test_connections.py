"""Session lifecycle, routing engine and the session manager."""

from __future__ import annotations

import pytest

from nexus_remote.connections.interfaces import (
    CommandResult,
    ConnectResult,
    ConnectionErrorKind,
    ProtocolAdapter,
    ProtocolError,
    RouteKind,
    SessionState,
)
from nexus_remote.connections.routing import RoutingEngine
from nexus_remote.connections.session import InvalidTransition, Session
from nexus_remote.devices.models import Device, ProtocolConfig
from nexus_remote.security.policies import PolicyEngine


class FakeAdapter(ProtocolAdapter):
    protocol = "ssh"

    def __init__(self, failure: ProtocolError | None = None) -> None:
        super().__init__()
        self.failure = failure
        self.target = None
        self.closed = False
        self.commands: list[str] = []

    def connect(self, target):
        self.target = target
        if self.failure is not None:
            raise self.failure
        self._connected = True
        return ConnectResult(
            ok=True,
            message="fake session established",
            server_banner="SSH-2.0-OpenSSH_test",
            auth_method="password",
            host_key_fingerprint="SHA256:fake",
        )

    def close(self) -> None:
        self._connected = False
        self.closed = True

    def execute(self, command: str, timeout: float = 30.0) -> CommandResult:
        self.commands.append(command)
        return CommandResult(command=command, exit_code=0, stdout="ok\n")


def _register(manager, adapter: FakeAdapter) -> None:
    manager.register_adapter("ssh", lambda: adapter)


def _session(device, state=SessionState.PENDING, **kwargs) -> Session:
    session = Session(
        device_id=device.id,
        device_name=device.name,
        protocol="ssh",
        host=device.hostname,
        port=22,
        **kwargs,
    )
    session.state = state
    return session


# -- state machine ---------------------------------------------------------
def test_session_transitions(device):
    session = _session(device)
    session.transition(SessionState.CONNECTING)
    session.transition(SessionState.CONNECTED)
    assert session.connected_at
    session.transition(SessionState.DISCONNECTING)
    session.transition(SessionState.CLOSED)
    assert session.closed_at
    assert session.active is False


def test_invalid_transition_is_rejected(device):
    session = _session(device)
    with pytest.raises(InvalidTransition):
        session.transition(SessionState.CONNECTED)


def test_failed_session_cannot_close_twice(device):
    session = _session(device, state=SessionState.CLOSED)
    with pytest.raises(InvalidTransition):
        session.transition(SessionState.CONNECTED)


def test_fail_records_error(device):
    session = _session(device, state=SessionState.CONNECTING)
    session.fail("boom", "timeout")
    assert session.state is SessionState.FAILED
    assert session.error == "boom"
    assert session.error_kind == "timeout"


def test_describe_is_human_readable(device):
    session = _session(device, state=SessionState.CONNECTED)
    assert "connected to" in session.describe()
    assert device.hostname in session.describe()


# -- routing ---------------------------------------------------------------
def test_routing_plan_starts_with_direct(device):
    plan = RoutingEngine(PolicyEngine()).plan(device)
    assert plan[0].method == "direct"
    assert plan[0].allowed


def test_next_route_skips_unavailable_and_attempted(device):
    device.routing.jump_host = "bastion:22"
    device.routing = type(device.routing)(
        preferred_method="direct", fallbacks=["relay", "jump_host"], jump_host="bastion:22"
    )
    engine = RoutingEngine(PolicyEngine())
    first = engine.next_route(device)
    assert first.method == "direct"
    second = engine.next_route(device, attempted={"direct"})
    assert second.method == "jump_host"  # relay is unconfigured, so skipped


def test_failure_summary_lists_each_route(device):
    device.routing.jump_host = "bastion:22"
    summary = RoutingEngine(PolicyEngine()).failure_summary(device, {"direct": "timed out"})
    assert "Could not reach" in summary
    assert "direct: failed - timed out" in summary
    assert "jump_host" in summary


# -- manager ---------------------------------------------------------------
def test_open_rejects_disabled_protocol(manager, device):
    with pytest.raises(ProtocolError):
        manager.open(device, "rdp")


def test_open_rejects_unregistered_adapter(manager, device):
    with pytest.raises(ProtocolError, match="adapter"):
        manager.open(device, "ssh")


def test_connect_without_stored_credential_fails_cleanly(manager, repository, device, audit):
    repository.add(device)
    _register(manager, FakeAdapter())
    session = manager.open(device, "ssh")
    result = manager.connect(session)
    assert result.state is SessionState.FAILED
    assert "no stored credential" in result.error
    assert result.error_kind == ConnectionErrorKind.AUTH.value
    assert repository.get(device.id).last_connection_ok is False
    assert any(event.action == "session.failed" for event in audit.events())


def test_connect_with_stored_credential_succeeds(manager, repository, credentials, device, audit):
    repository.add(device)
    credentials.save(device.id, "password", "deploy", "secret-value")
    adapter = FakeAdapter()
    _register(manager, adapter)
    session = manager.connect(manager.open(device, "ssh"))
    assert session.state is SessionState.CONNECTED
    assert session.server_banner.startswith("SSH-2.0")
    assert adapter.target.secret == "secret-value"
    assert repository.get(device.id).last_connection_ok is True
    assert any(event.action == "session.connected" for event in audit.events())


def test_password_prompt_secret_is_used_once(manager, repository, device):
    repository.add(device)
    adapter = FakeAdapter()
    _register(manager, adapter)
    session = manager.open(device, "ssh", secret="one-time-secret")
    manager.connect(session)
    assert adapter.target.secret == "one-time-secret"
    assert "secret" not in session.metadata


def test_adapter_failure_is_recorded(manager, repository, credentials, device, audit):
    repository.add(device)
    credentials.save(device.id, "password", "deploy", "secret-value")
    failure = ProtocolError("authentication failed", kind=ConnectionErrorKind.AUTH)
    _register(manager, FakeAdapter(failure=failure))
    session = manager.connect(manager.open(device, "ssh"))
    assert session.state is SessionState.FAILED
    assert session.error_kind == "auth"
    assert any(event.action == "session.failed" for event in audit.events())


def test_failed_session_can_retry(manager, repository, credentials, device):
    repository.add(device)
    credentials.save(device.id, "password", "deploy", "secret-value")
    failing = FakeAdapter(ProtocolError("timeout", kind=ConnectionErrorKind.TIMEOUT))
    manager.register_adapter("ssh", lambda: failing)
    session = manager.connect(manager.open(device, "ssh"))
    assert session.state is SessionState.FAILED

    manager.register_adapter("ssh", lambda: FakeAdapter())
    session = manager.connect(session)
    assert session.state is SessionState.CONNECTED


def test_execute_read_only_command(manager, repository, credentials, device, history):
    repository.add(device)
    credentials.save(device.id, "password", "deploy", "secret-value")
    _register(manager, FakeAdapter())
    session = manager.connect(manager.open(device, "ssh"))
    result, reason = manager.execute(session, "ls -la", environment="bash")
    assert result is not None and result.ok
    assert reason
    assert [record.command for record in history.recent()] == ["ls -la"]
    assert history.recent()[0].status == "executed"


def test_execute_destructive_command_is_denied(manager, repository, credentials, device, audit):
    repository.add(device)
    credentials.save(device.id, "password", "deploy", "secret-value")
    adapter = FakeAdapter()
    _register(manager, adapter)
    session = manager.connect(manager.open(device, "ssh"))
    result, reason = manager.execute(session, "rm -rf /", environment="bash")
    assert result is None
    assert "destructive" in reason
    assert adapter.commands == []
    assert any(event.action == "command.denied" for event in audit.events())


def test_execute_requires_connection(manager, device):
    session = _session(device)
    with pytest.raises(ProtocolError):
        manager.execute(session, "ls")


def test_disconnect_closes_adapter(manager, repository, credentials, device, audit):
    repository.add(device)
    credentials.save(device.id, "password", "deploy", "secret-value")
    adapter = FakeAdapter()
    _register(manager, adapter)
    session = manager.connect(manager.open(device, "ssh"))
    manager.disconnect(session)
    assert session.state is SessionState.CLOSED
    assert adapter.closed is True
    assert any(event.action == "session.closed" for event in audit.events())


def test_active_session_accounting(manager, repository, credentials, device):
    repository.add(device)
    credentials.save(device.id, "password", "deploy", "secret-value")
    _register(manager, FakeAdapter())
    session = manager.connect(manager.open(device, "ssh"))
    assert manager.count_active() == 1
    assert manager.get(session.id) is session
    manager.disconnect(session)
    assert manager.count_active() == 0


def test_route_kind_is_recorded(manager, repository, device):
    repository.add(device)
    _register(manager, FakeAdapter())
    session = manager.open(device, "ssh", route_method="vpn")
    assert session.route is RouteKind.VPN


def test_devices_with_no_protocol_cannot_open(manager, repository):
    bare = Device(
        name="bare",
        hostname="10.0.0.1",
        connections={"ssh": ProtocolConfig("ssh", enabled=False)},
    )
    # Bypass validation for this edge case: open() must still refuse.
    bare.connections["ssh"].enabled = False
    with pytest.raises(ProtocolError):
        manager.open(bare)
