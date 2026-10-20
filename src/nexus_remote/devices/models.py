"""Device, connection-profile and routing models with validation.

These are plain dataclasses so the inventory can be exercised by tests and by
the import/export path without starting a GUI or a database.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from nexus_remote import config
from nexus_remote.util import json_dumps, utcnow_iso

OPERATING_SYSTEMS = ("windows", "linux", "macos", "network", "cloud", "unknown")

ENVIRONMENTS = ("production", "staging", "development", "testing", "lab", "unspecified")

CRITICALITIES = ("critical", "high", "standard", "low")

#: Protocols the application can talk to. ``ssh``/``sftp``/``rdp``/``smb`` are
#: implemented over phases 2 and 6, ``serial`` opens a local COM/tty console;
#: the rest are reserved for later adapters.
PROTOCOLS = ("ssh", "sftp", "rdp", "smb", "serial", "vnc", "winrm", "https")

#: How a protocol authenticates. Secret material always lives in the vault.
AUTH_METHODS = (
    "credential_vault",
    "ssh_key",
    "ssh_agent",
    "keyboard_interactive",
    "password_prompt",
    "none",
)

#: Routing methods, ordered from most direct to most indirect.
ROUTE_METHODS = ("direct", "vpn", "jump_host", "rdp_gateway", "cloud_private", "agent", "relay")

DEFAULT_PORTS = {
    "ssh": config.DEFAULT_SSH_PORT,
    "sftp": config.DEFAULT_SFTP_PORT,
    "rdp": config.DEFAULT_RDP_PORT,
    "smb": config.DEFAULT_SMB_PORT,
    "vnc": 5900,
    "winrm": 5985,
    "https": 443,
}

#: Protocols a fresh device profile starts with. RDP/SMB stay opted out until
#: the user enables them, per the security-first design.
DEFAULT_ENABLED = ("ssh", "sftp")

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"([A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?)"
    r"(\.[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?)*\.?$"
)


class ValidationError(ValueError):
    """Raised when a device profile fails validation."""


class ProfileError(ValueError):
    """Raised when an import/export payload is malformed."""


def normalize_host(value: str) -> str:
    """Validate and canonicalise an IPv4/IPv6 address or DNS hostname."""
    if value is None:
        raise ValidationError("host is required")
    host = str(value).strip()
    if not host:
        raise ValidationError("host is required")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    candidate = host.rstrip(".")
    if _HOSTNAME_RE.match(candidate):
        return candidate.lower()
    raise ValidationError(f"{value!r} is not a valid IP address or hostname")


_SERIAL_PORT_RE = re.compile(
    r"^(?:\\\\\.\\)?(COM\d{1,3})$|^(/dev/(?:tty[A-Za-z0-9._-]+|serial/by-[A-Za-z0-9._/-]+))$",
    re.IGNORECASE,
)


def is_serial_port(value: str) -> bool:
    """True for Windows COM ports (``COM3``, ``\\\\.\\COM3``) and POSIX ttys."""
    return bool(_SERIAL_PORT_RE.match(str(value or "").strip()))


def normalize_serial_port(value: str) -> str | None:
    """Canonical serial port name, or ``None`` when it is not a port name."""
    text = str(value or "").strip()
    if not text or not is_serial_port(text):
        return None
    if text.upper().replace("\\\\.\\", "").startswith("COM"):
        return text.replace("\\\\.\\", "").upper()
    return text


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _check_choice(value: str, choices: Iterable[str], label: str) -> str:
    text = str(value).strip().lower()
    if text not in choices:
        raise ValidationError(f"{label} must be one of: {', '.join(choices)} (got {value!r})")
    return text


@dataclass
class ProtocolConfig:
    """One protocol endpoint for a device."""

    protocol: str
    enabled: bool = True
    port: int | None = None
    auth_method: str = "credential_vault"
    username: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.protocol = _check_choice(self.protocol, PROTOCOLS, "protocol")
        self.auth_method = _check_choice(self.auth_method, AUTH_METHODS, "authentication method")
        if self.port is None:
            self.port = DEFAULT_PORTS.get(self.protocol)
        elif not 0 < int(self.port) < 65536:
            raise ValidationError(f"port for {self.protocol} must be between 1 and 65535")
        else:
            self.port = int(self.port)
        self.enabled = bool(self.enabled)
        self.username = str(self.username or "")
        self.extra = dict(self.extra or {})

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "enabled": self.enabled,
            "port": self.port,
            "authentication": self.auth_method,
        }
        if self.username:
            payload["username"] = self.username
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload

    @classmethod
    def from_dict(cls, protocol: str, payload: dict[str, Any] | None) -> "ProtocolConfig":
        data = dict(payload or {})
        return cls(
            protocol=protocol,
            enabled=bool(data.get("enabled", True)),
            port=data.get("port"),
            auth_method=data.get("authentication", data.get("auth_method", "credential_vault")),
            username=data.get("username", ""),
            extra=data.get("extra", {}) or {},
        )


@dataclass
class RoutingPreferences:
    """How the routing engine may reach this device, in order of preference."""

    preferred_method: str = "direct"
    fallbacks: list[str] = field(default_factory=list)
    jump_host: str | None = None
    gateway: str | None = None
    relay: str | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        self.preferred_method = _check_choice(self.preferred_method, ROUTE_METHODS, "preferred route")
        cleaned: list[str] = []
        for method in self.fallbacks or []:
            fallback = _check_choice(method, ROUTE_METHODS, "fallback route")
            if fallback == self.preferred_method or fallback in cleaned:
                continue
            cleaned.append(fallback)
        self.fallbacks = cleaned
        for name in ("jump_host", "gateway", "relay", "agent_id"):
            value = getattr(self, name)
            setattr(self, name, str(value).strip() or None if value else None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preferred_method": self.preferred_method,
            "fallbacks": list(self.fallbacks),
            "jump_host": self.jump_host,
            "gateway": self.gateway,
            "relay": self.relay,
            "agent_id": self.agent_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "RoutingPreferences":
        data = dict(payload or {})
        return cls(
            preferred_method=data.get("preferred_method", "direct"),
            fallbacks=list(data.get("fallbacks") or []),
            jump_host=data.get("jump_host"),
            gateway=data.get("gateway"),
            relay=data.get("relay"),
            agent_id=data.get("agent_id"),
        )


def default_connections() -> dict[str, ProtocolConfig]:
    return {
        protocol: ProtocolConfig(protocol=protocol, enabled=protocol in DEFAULT_ENABLED)
        for protocol in ("ssh", "sftp", "rdp", "smb", "serial")
    }


@dataclass
class Device:
    """A saved device profile."""

    name: str
    hostname: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    operating_system: str = "unknown"
    environment: str = "unspecified"
    criticality: str = "standard"
    tags: list[str] = field(default_factory=list)
    description: str = ""
    owner: str = ""
    notes: str = ""
    favorite: bool = False
    group_id: str | None = None
    group_name: str = ""
    connections: dict[str, ProtocolConfig] = field(default_factory=default_connections)
    routing: RoutingPreferences = field(default_factory=RoutingPreferences)
    last_connected_at: str | None = None
    last_connection_ok: bool | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def validate(self) -> "Device":
        self.name = str(self.name or "").strip()
        if not self.name:
            raise ValidationError("device name is required")
        if len(self.name) > 120:
            raise ValidationError("device name must be 120 characters or fewer")
        # A serial-only profile uses its port name as the address, so check for
        # one first (``COM3`` would otherwise pass as a lower-cased hostname).
        serial_port = normalize_serial_port(self.hostname)
        self.hostname = serial_port if serial_port is not None else normalize_host(self.hostname)
        self.operating_system = _check_choice(self.operating_system, OPERATING_SYSTEMS, "operating system")
        self.environment = _check_choice(self.environment, ENVIRONMENTS, "environment")
        self.criticality = _check_choice(self.criticality, CRITICALITIES, "criticality")
        self.tags = sorted({str(tag).strip() for tag in (self.tags or []) if str(tag).strip()})
        self.description = str(self.description or "").strip()
        self.owner = str(self.owner or "").strip()
        self.notes = str(self.notes or "")
        self.favorite = bool(self.favorite)

        connections = dict(self.connections or {})
        for protocol, cfg in list(connections.items()):
            if not isinstance(cfg, ProtocolConfig):
                connections[protocol] = ProtocolConfig.from_dict(protocol, cfg)  # type: ignore[arg-type]
            if connections[protocol].protocol != protocol:
                raise ValidationError(f"connection key {protocol!r} does not match its protocol")
        if not connections:
            connections = default_connections()
        self.connections = connections
        if not any(cfg.enabled for cfg in self.connections.values()):
            raise ValidationError("at least one connection protocol must be enabled")
        if not isinstance(self.routing, RoutingPreferences):
            self.routing = RoutingPreferences.from_dict(self.routing)  # type: ignore[arg-type]
        RoutingPreferences(**self.routing.to_dict())  # re-validate after mutation
        self.touch()
        return self

    def touch(self) -> None:
        self.updated_at = utcnow_iso()

    # -- convenience --------------------------------------------------------
    def connection(self, protocol: str) -> ProtocolConfig | None:
        return self.connections.get(protocol)

    def enabled_protocols(self) -> list[str]:
        return [name for name, cfg in self.connections.items() if cfg.enabled]

    def primary_protocol(self) -> str | None:
        for candidate in ("ssh", "rdp", "smb", "sftp", "serial", "vnc", "winrm", "https"):
            cfg = self.connections.get(candidate)
            if cfg and cfg.enabled:
                return candidate
        return None

    def endpoint(self, protocol: str | None = None) -> tuple[str, int | None]:
        name = protocol or self.primary_protocol()
        cfg = self.connections.get(name) if name else None
        return self.hostname, (cfg.port if cfg else None)

    def search_blob(self) -> str:
        parts = [
            self.name,
            self.hostname,
            self.operating_system,
            self.environment,
            self.criticality,
            self.description,
            self.owner,
            " ".join(self.tags),
            self.group_name,
        ]
        return " ".join(part for part in parts if part).casefold()

    def to_dict(self, *, include_local_state: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "hostname": self.hostname,
            "operating_system": self.operating_system,
            "environment": self.environment,
            "criticality": self.criticality,
            "tags": list(self.tags),
            "description": self.description,
            "owner": self.owner,
            "notes": self.notes,
            "favorite": self.favorite,
            "connections": {name: cfg.to_dict() for name, cfg in self.connections.items()},
            "routing": self.routing.to_dict(),
        }
        if include_local_state:
            payload.update(
                {
                    "id": self.id,
                    "group": self.group_name or None,
                    "last_connected_at": self.last_connected_at,
                    "last_connection_ok": self.last_connection_ok,
                    "created_at": self.created_at,
                    "updated_at": self.updated_at,
                }
            )
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Device":
        data = dict(payload or {})
        connections = {
            name: ProtocolConfig.from_dict(name, cfg)
            for name, cfg in dict(data.get("connections") or {}).items()
        }
        device = cls(
            id=str(data.get("id") or uuid.uuid4().hex),
            name=data.get("name", ""),
            hostname=data.get("hostname", ""),
            operating_system=data.get("operating_system", data.get("os", "unknown")),
            environment=data.get("environment", "unspecified"),
            criticality=data.get("criticality", data.get("classification", "standard")),
            tags=list(data.get("tags") or []),
            description=data.get("description", ""),
            owner=data.get("owner", ""),
            notes=data.get("notes", ""),
            favorite=bool(data.get("favorite", False)),
            group_name=str(data.get("group") or ""),
            connections=connections or default_connections(),
            routing=RoutingPreferences.from_dict(data.get("routing")),
            last_connected_at=data.get("last_connected_at"),
            last_connection_ok=data.get("last_connection_ok"),
            created_at=str(data.get("created_at") or utcnow_iso()),
            updated_at=str(data.get("updated_at") or utcnow_iso()),
        )
        return device.validate()

    def signature(self) -> str:
        """Identity used for duplicate detection (host + protocol + port)."""
        endpoints = sorted(
            f"{name}:{cfg.port}" for name, cfg in self.connections.items() if cfg.enabled
        )
        return f"{self.hostname}|{'|'.join(endpoints)}"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.name} ({self.hostname})"

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Device(name={self.name!r}, hostname={self.hostname!r}, env={self.environment!r})"


def connection_json(connections: dict[str, ProtocolConfig]) -> str:
    return json_dumps({name: cfg.to_dict() for name, cfg in connections.items()})
