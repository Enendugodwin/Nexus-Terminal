"""Session manager: creates, tracks and tears down protocol sessions."""

from __future__ import annotations

import threading
from typing import Callable

from nexus_remote.assistant.command_history import CommandHistory, CommandStatus
from nexus_remote.connections.interfaces import (
    CommandResult,
    ConnectionErrorKind,
    ConnectionTarget,
    ProtocolAdapter,
    ProtocolError,
    RouteKind,
    SessionState,
)
from nexus_remote.connections.routing import RoutingEngine
from nexus_remote.connections.session import InvalidTransition, Session
from nexus_remote.devices.models import PROTOCOLS, Device
from nexus_remote.devices.repository import DeviceRepository
from nexus_remote.security.audit import AuditLog
from nexus_remote.security.credentials import CredentialError, CredentialService
from nexus_remote.security.policies import PolicyEngine, RiskLevel

AdapterFactory = Callable[[], ProtocolAdapter]

#: Session-level credential kind per protocol.
CREDENTIAL_KIND = {
    "ssh": "password",
    "sftp": "password",
    "rdp": "password",
    "smb": "password",
    "vnc": "password",
    "winrm": "password",
    "https": "api_token",
}


class SessionManager:
    """Owns adapter instances and session lifecycle.

    The manager is synchronous: the UI runs :meth:`connect` on a worker thread
    so the interface stays responsive.
    """

    def __init__(
        self,
        repository: DeviceRepository,
        *,
        credentials: CredentialService | None = None,
        audit: AuditLog | None = None,
        policy: PolicyEngine | None = None,
        routing: RoutingEngine | None = None,
        history: CommandHistory | None = None,
    ) -> None:
        self.repository = repository
        self.credentials = credentials
        self.audit = audit
        self.policy = policy or PolicyEngine()
        self.routing = routing or RoutingEngine(self.policy)
        self.history = history
        self._adapters: dict[str, AdapterFactory] = {}
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    # -- adapter registry ---------------------------------------------------
    def register_adapter(self, protocol: str, factory: AdapterFactory) -> None:
        if protocol not in PROTOCOLS:
            raise ValueError(f"unsupported protocol {protocol!r}")
        self._adapters[protocol] = factory

    def supported(self, protocol: str) -> bool:
        return protocol in self._adapters

    def available_protocols(self) -> list[str]:
        return sorted(self._adapters)

    # -- lifecycle ----------------------------------------------------------
    def open(
        self,
        device: Device,
        protocol: str | None = None,
        *,
        route_method: str | None = None,
        username: str | None = None,
        secret: str | None = None,
        options: dict[str, object] | None = None,
    ) -> Session:
        """Create a pending session (no network activity yet)."""
        chosen = protocol or device.primary_protocol()
        if not chosen:
            raise ProtocolError("the device has no enabled connection protocol")
        cfg = device.connection(chosen)
        if cfg is None or not cfg.enabled:
            raise ProtocolError(f"{chosen} is not enabled for {device.name}", kind=ConnectionErrorKind.POLICY)
        if chosen not in self._adapters:
            raise ProtocolError(
                f"the {chosen} adapter is not available in this build",
                kind=ConnectionErrorKind.POLICY,
            )
        method = route_method or device.routing.preferred_method
        session = Session(
            device_id=device.id,
            device_name=device.name,
            protocol=chosen,
            host=device.hostname,
            port=cfg.port,
            route=_route_kind(method),
            username=username if username is not None else cfg.username,
            metadata={"auth_method": cfg.auth_method, "options": dict(options or cfg.extra)},
        )
        if secret is not None:
            session.metadata["secret"] = secret  # held in memory only
        with self._lock:
            self._sessions[session.id] = session
        if self.audit:
            self.audit.record(
                "session",
                "session.opened",
                outcome="info",
                device_id=device.id,
                subject=f"{chosen} -> {session.endpoint}",
                detail={"route": session.route.value, "username": session.username, "state": session.state.value},
            )
        return session

    def connect(self, session: Session) -> Session:
        """Connect a pending session; failure is recorded on the session."""
        if session.state is SessionState.CONNECTED:
            return session
        if session.state is SessionState.CONNECTING:
            return session
        device = self.repository.get(session.device_id)
        if device is None:
            session.fail("the device profile no longer exists", ConnectionErrorKind.POLICY.value)
            return session

        try:
            session.transition(SessionState.CONNECTING)
        except InvalidTransition:
            # A failed session may be retried.
            session.state = SessionState.CONNECTING

        try:
            target = self._build_target(device, session)
        except ProtocolError as exc:
            self._record_failure(session, device, exc)
            return session
        adapter = self._adapters[session.protocol]()
        session._adapter = adapter
        try:
            result = adapter.connect(target)
        except ProtocolError as exc:
            self._record_failure(session, device, exc)
            return session
        except Exception as exc:  # pragma: no cover - defensive
            failure = ProtocolError(str(exc), kind=ConnectionErrorKind.UNKNOWN)
            self._record_failure(session, device, failure)
            return session

        session.server_banner = result.server_banner
        session.host_key_fingerprint = result.host_key_fingerprint
        session.metadata["auth_method"] = result.auth_method
        session.transition(SessionState.CONNECTED)
        self.repository.record_connection(device.id, True)
        if self.audit:
            self.audit.record(
                "session",
                "session.connected",
                outcome="success",
                device_id=device.id,
                subject=f"{session.protocol} {session.endpoint}",
                detail={
                    "route": session.route.value,
                    "auth_method": result.auth_method,
                    "host_key": result.host_key_fingerprint,
                },
            )
        return session

    def disconnect(self, session: Session) -> Session:
        adapter = session._adapter
        if session.state is SessionState.CONNECTED:
            session.transition(SessionState.DISCONNECTING)
        try:
            if adapter is not None:
                adapter.close()
        finally:
            if session.state is not SessionState.CLOSED:
                try:
                    session.transition(SessionState.CLOSED)
                except InvalidTransition:
                    session.state = SessionState.CLOSED
            session._adapter = None
        if self.audit:
            self.audit.record(
                "session",
                "session.closed",
                outcome="success",
                device_id=session.device_id,
                subject=f"{session.protocol} {session.endpoint}",
                detail={"route": session.route.value},
            )
        return session

    def close_all(self) -> None:
        for session in list(self._sessions.values()):
            if session.active:
                self.disconnect(session)

    # -- commands -----------------------------------------------------------
    def execute(
        self,
        session: Session,
        command: str,
        *,
        approved: bool = False,
        timeout: float = 30.0,
        environment: str = "unknown",
    ) -> tuple[CommandResult | None, str]:
        """Run a command through the policy gate.

        Returns ``(result, message)``; ``result`` is ``None`` when policy
        blocked the command.
        """
        adapter = session._adapter
        if adapter is None or session.state is not SessionState.CONNECTED:
            raise ProtocolError("the session is not connected")
        decision = self.policy.evaluate(command, environment, approved=approved)
        if not decision.allowed:
            if self.audit:
                self.audit.record(
                    "policy",
                    "command.denied",
                    severity="warning",
                    outcome="denied",
                    device_id=session.device_id,
                    subject=command,
                    detail={"risk": decision.risk.value, "reason": decision.reason},
                )
            return None, decision.reason
        result = adapter.execute(command, timeout=timeout)
        if self.history:
            record_id = self.history.record(
                command,
                device_id=session.device_id,
                environment=environment,
                status=CommandStatus.EXECUTED,
                source="user",
                exit_code=result.exit_code,
                risk=decision.risk.value,
            )
            session.metadata["last_history_id"] = record_id
        if self.audit:
            self.audit.record(
                "session",
                "command.executed",
                outcome="success" if result.ok else "failure",
                device_id=session.device_id,
                subject=command,
                detail={"risk": decision.risk.value, "exit_code": result.exit_code},
            )
        return result, decision.reason

    # -- queries ------------------------------------------------------------
    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def sessions(self) -> list[Session]:
        return list(self._sessions.values())

    def active_sessions(self) -> list[Session]:
        return [session for session in self._sessions.values() if session.active]

    def count_active(self) -> int:
        return len(self.active_sessions())

    # -- internals ----------------------------------------------------------
    def _build_target(self, device: Device, session: Session) -> ConnectionTarget:
        cfg = device.connection(session.protocol)
        secret = session.metadata.pop("secret", None)
        if secret is None and cfg is not None:
            secret = self._resolve_secret(device, session)
        options = dict(cfg.extra) if cfg else {}
        options.update(dict(session.metadata.get("options") or {}))
        return ConnectionTarget(
            host=device.hostname,
            protocol=session.protocol,
            port=session.port,
            username=session.username,
            secret=str(secret) if secret is not None else None,
            route=session.route,
            jump_host=device.routing.jump_host,
            options=options,
        )

    def _resolve_secret(self, device: Device, session: Session) -> str | None:
        if self.credentials is None:
            return None
        cfg = device.connection(session.protocol)
        auth = cfg.auth_method if cfg else "credential_vault"
        if auth in {"none", "ssh_agent"}:
            return None
        kind = CREDENTIAL_KIND.get(session.protocol, "password")
        try:
            secret = self.credentials.resolve(device.id, kind, session.username)
        except CredentialError as exc:
            raise ProtocolError(f"the credential vault is unavailable: {exc}", kind=ConnectionErrorKind.POLICY) from exc
        if secret is None and auth == "credential_vault":
            raise ProtocolError(
                f"no stored credential for {device.name}. Add one in the Security Center or "
                "connect with an interactive prompt.",
                kind=ConnectionErrorKind.AUTH,
            )
        return secret

    def _record_failure(self, session: Session, device: Device, error: ProtocolError) -> None:
        session.fail(str(error), error.kind.value)
        self.repository.record_connection(device.id, False)
        if self.audit:
            self.audit.record(
                "session",
                "session.failed",
                severity="warning",
                outcome="failure",
                device_id=device.id,
                subject=f"{session.protocol} {session.endpoint}",
                detail={"kind": error.kind.value, "message": str(error), "detail": error.detail},
            )


def _route_kind(method: str) -> RouteKind:
    try:
        return RouteKind(method)
    except ValueError:
        return RouteKind.DIRECT


def risk_of(command: str, environment: str = "unknown") -> RiskLevel:
    return PolicyEngine.classify(command, environment)
