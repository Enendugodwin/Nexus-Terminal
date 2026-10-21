"""Session state machine.

Transitions are validated so a session cannot silently jump from ``failed`` to
``connected`` or be closed twice.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from nexus_remote.connections.interfaces import RouteKind, SessionState
from nexus_remote.util import utcnow_iso

_ALLOWED: dict[SessionState, set[SessionState]] = {
    SessionState.PENDING: {SessionState.CONNECTING, SessionState.CLOSED, SessionState.FAILED},
    SessionState.CONNECTING: {SessionState.CONNECTED, SessionState.FAILED, SessionState.CLOSED},
    SessionState.CONNECTED: {SessionState.DISCONNECTING, SessionState.FAILED},
    SessionState.DISCONNECTING: {SessionState.CLOSED, SessionState.FAILED},
    SessionState.CLOSED: set(),
    SessionState.FAILED: {SessionState.CONNECTING, SessionState.CLOSED},
}

StateListener = Callable[["Session"], None]


class InvalidTransition(RuntimeError):
    pass


@dataclass
class Session:
    device_id: str
    device_name: str
    protocol: str
    host: str
    port: int | None = None
    route: RouteKind = RouteKind.DIRECT
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: SessionState = SessionState.PENDING
    username: str = ""
    error: str = ""
    error_kind: str = ""
    created_at: str = field(default_factory=utcnow_iso)
    connected_at: str | None = None
    closed_at: str | None = None
    server_banner: str = ""
    host_key_fingerprint: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    #: Not part of the serialised state; wired by the manager.
    _listeners: list[StateListener] = field(default_factory=list, repr=False, compare=False)
    _adapter: object | None = field(default=None, repr=False, compare=False)

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}" if self.port else self.host

    @property
    def active(self) -> bool:
        return self.state in {SessionState.CONNECTING, SessionState.CONNECTED, SessionState.DISCONNECTING}

    @property
    def adapter(self) -> object | None:
        """The live protocol adapter, or None when the session is not connected."""
        return self._adapter

    def subscribe(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def transition(self, new_state: SessionState, *, reason: str = "") -> None:
        if new_state is self.state:
            return
        if new_state not in _ALLOWED[self.state]:
            raise InvalidTransition(f"{self.protocol} session cannot go {self.state.value} -> {new_state.value}")
        self.state = new_state
        if new_state is SessionState.CONNECTED:
            self.connected_at = utcnow_iso()
        elif new_state in {SessionState.CLOSED, SessionState.FAILED}:
            self.closed_at = utcnow_iso()
        if reason and new_state is SessionState.FAILED:
            self.error = reason
        for listener in list(self._listeners):
            listener(self)

    def fail(self, message: str, kind: str = "") -> None:
        self.error = message
        self.error_kind = kind
        try:
            self.transition(SessionState.FAILED, reason=message)
        except InvalidTransition:
            self.state = SessionState.FAILED

    def describe(self) -> str:
        state = self.state.value
        if self.state is SessionState.CONNECTED:
            return f"{self.protocol.upper()} connected to {self.endpoint} via {self.route.value}"
        if self.state is SessionState.FAILED:
            return f"{self.protocol.upper()} failed: {self.error or 'unknown error'}"
        return f"{self.protocol.upper()} {state} ({self.endpoint})"
