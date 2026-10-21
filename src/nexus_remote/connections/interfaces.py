"""Connection-layer types shared by adapters, the router and the UI."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionState(str, Enum):
    PENDING = "pending"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    CLOSED = "closed"
    FAILED = "failed"


class RouteKind(str, Enum):
    DIRECT = "direct"
    VPN = "vpn"
    JUMP_HOST = "jump_host"
    RDP_GATEWAY = "rdp_gateway"
    CLOUD_PRIVATE = "cloud_private"
    AGENT = "agent"
    RELAY = "relay"


class ConnectionErrorKind(str, Enum):
    DNS = "dns"
    ROUTE = "route"
    TIMEOUT = "timeout"
    REFUSED = "refused"
    AUTH = "auth"
    HOST_KEY = "host_key"
    PROTOCOL = "protocol"
    POLICY = "policy"
    UNKNOWN = "unknown"


class ProtocolError(RuntimeError):
    """Structured failure raised by a protocol adapter.

    ``kind`` drives the diagnosis the UI offers; ``detail`` is safe to log
    (adapters must never put credentials in it).
    """

    def __init__(
        self,
        message: str,
        *,
        kind: ConnectionErrorKind = ConnectionErrorKind.UNKNOWN,
        detail: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail
        self.retryable = retryable


@dataclass
class ConnectionTarget:
    """Everything an adapter needs to open a session.

    ``secret`` is resolved from the vault immediately before connecting and is
    never persisted or logged.
    """

    host: str
    protocol: str
    port: int | None = None
    username: str = ""
    secret: str | None = None
    private_key: str | None = None
    passphrase: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    route: RouteKind = RouteKind.DIRECT
    jump_host: str | None = None

    def endpoint(self) -> str:
        return f"{self.host}:{self.port}" if self.port else self.host

    def __repr__(self) -> str:  # never leak secrets in logs or tracebacks
        return (
            f"ConnectionTarget(host={self.host!r}, protocol={self.protocol!r}, "
            f"port={self.port!r}, username={self.username!r}, route={self.route.value!r})"
        )


@dataclass
class ConnectResult:
    ok: bool
    message: str = ""
    server_banner: str = ""
    auth_method: str = ""
    host_key_fingerprint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandResult:
    command: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def combined(self) -> str:
        return (self.stdout or "") + (("\n" + self.stderr) if self.stderr else "")


class ProtocolAdapter(ABC):
    """Interface every protocol implementation must satisfy.

    The session manager and UI depend on this, not on paramiko or FreeRDP, so a
    library can be replaced without touching the rest of the application.
    """

    protocol: str = "abstract"

    def __init__(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @abstractmethod
    def connect(self, target: ConnectionTarget) -> ConnectResult:
        """Open the session or raise :class:`ProtocolError`."""

    @abstractmethod
    def close(self) -> None:
        """Close the session; must be safe to call twice."""

    def execute(self, command: str, timeout: float = 30.0) -> CommandResult:
        raise NotImplementedError(f"{self.protocol} adapter does not support command execution")

    def open_shell(self, term_size: tuple[int, int] = (120, 32)) -> Any:
        raise NotImplementedError(f"{self.protocol} adapter does not support interactive shells")

    def __enter__(self) -> ProtocolAdapter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
