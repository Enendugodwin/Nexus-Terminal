"""Connection layer: interfaces, session state, routing and orchestration."""

from nexus_remote.connections.interfaces import (
    CommandResult,
    ConnectionErrorKind,
    ConnectionTarget,
    ConnectResult,
    ProtocolAdapter,
    ProtocolError,
    RouteKind,
    SessionState,
)
from nexus_remote.connections.manager import SessionManager
from nexus_remote.connections.routing import RouteAttempt, RoutingEngine
from nexus_remote.connections.session import InvalidTransition, Session

__all__ = [
    "CommandResult",
    "ConnectResult",
    "ConnectionErrorKind",
    "ConnectionTarget",
    "InvalidTransition",
    "ProtocolAdapter",
    "ProtocolError",
    "RouteAttempt",
    "RouteKind",
    "RoutingEngine",
    "Session",
    "SessionManager",
    "SessionState",
]
