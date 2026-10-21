"""Deterministic policy engine.

Policy - not the assistant - decides what is allowed: how risky a command is,
which routes may be attempted, and which destinations a relay may reach. The
engine is deliberately rule-based, testable and offline.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from nexus_remote.devices.models import Device, RoutingPreferences, is_ip_literal


class RiskLevel(str, Enum):
    READ_ONLY = "read-only"
    MODIFIES_STATE = "modifies-state"
    DESTRUCTIVE = "destructive"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {
            RiskLevel.READ_ONLY: 0,
            RiskLevel.UNKNOWN: 1,
            RiskLevel.MODIFIES_STATE: 2,
            RiskLevel.DESTRUCTIVE: 3,
        }[self]


#: Commands that can destroy data or lock the operator out. Never auto-run.
DESTRUCTIVE_PATTERNS: tuple[str, ...] = (
    r"\brm\s+(-[a-z]*\s+)*-?[rf]{1,2}\b",
    r"\brm\s+-[a-z]*r[a-z]*f",
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\b.*\bof=",
    r"\b(shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b",
    r"\bformat\s+[a-z]:",
    r"\bdiskpart\b",
    r"\bdel\s+/[a-z]*[sq]",
    r"\bRemove-Item\b.*-Recurse.*-Force",
    r"\brd\s+/s\b",
    r"\bchmod\s+-R\s+777\b",
    r"\bchown\s+-R\b",
    r"\biptables\s+-F\b",
    r"\bufw\s+disable\b",
    r"\b(firewall-cmd)\b.*--remove",
    r"\bnetsh\s+advfirewall\b.*(off|delete|reset)",
    r"\bsystemctl\s+(stop|disable|mask)\b",
    r"\b(service)\s+\S+\s+stop\b",
    r"\bStop-Service\b",
    r"\b(net\s+user|useradd|adduser|userdel|deluser)\b",
    r"\bpasswd\b",
    r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b",
    r"\bTRUNCATE\s+TABLE\b",
    r"\bDELETE\s+FROM\b(?!.*\bWHERE\b)",
    r":\(\)\s*\{.*\};:",
    r"\b>\s*/dev/sd[a-z]",
    r"\b(razor|wipefs)\b",
    r"\bVACUUM\s+INTO\b",
)

#: Commands that change configuration or state but are usually recoverable.
STATE_PATTERNS: tuple[str, ...] = (
    r"\b(apt|apt-get|yum|dnf|pacman|zypper)\s+(install|remove|purge|upgrade)\b",
    r"\bpip\s+install\b",
    r"\bchoco\s+install\b",
    r"\bwinget\s+install\b",
    r"\b(Set-|New-|Add-|Start-|Restart-|Stop-|Enable-|Disable-)[A-Za-z]+",
    r"\b(netsh|sc|reg)\s+(add|delete|set|config)\b",
    r"\b(systemctl|service)\s+(start|restart|enable)\b",
    r"\b(kubectl|docker|podman)\s+(apply|create|delete|run|rm|exec|restart)\b",
    r"\bgit\s+(push|reset|checkout|clean|commit)\b",
    r"\bmount\b|\bumount\b",
    r"\bcrontab\b",
    r"\btee\s+/",
    r"\b>\s*(?!/dev/null)\S+",
    r"\b(curl|wget)\b.*\|\s*(sudo\s+)?(ba)?sh",
    r"\bsudo\b",
    r"\brunas\b",
    r"\bNew-ItemProperty\b",
)

_READ_ONLY_HINTS = (
    "ls", "dir", "cat", "type", "less", "more", "head", "tail", "pwd", "whoami",
    "hostname", "id", "uname", "uptime", "date", "who", "w", "last", "df", "du",
    "free", "top", "htop", "ps", "tasklist", "ipconfig", "ip ", "ifconfig",
    "netstat", "ss ", "ping", "tracert", "traceroute", "nslookup", "dig", "host ",
    "arp", "route print", "getmac", "systeminfo", "ver", "env", "set", "printenv",
    "history", "lsblk", "lsof", "mount -l", "getent", "systemctl status",
    "journalctl", "dmesg", "show", "display", "Get-", "Test-NetConnection",
    "Resolve-DnsName", "Get-NetAdapter", "gpresult", "kubectl get", "docker ps",
    "docker images", "git status", "git log", "git diff", "wc", "grep", "find",
    "awk", "sed -n", "sort", "uniq", "cut", "file", "stat", "sha256sum", "md5sum",
)

#: Route methods the routing engine may attempt, in preference order.
ROUTE_ORDER = ("direct", "vpn", "jump_host", "rdp_gateway", "cloud_private", "agent", "relay")


@dataclass(frozen=True)
class ApprovalDecision:
    allowed: bool
    risk: RiskLevel
    requires_confirmation: bool
    reason: str
    environment: str = "unknown"

    @property
    def blocked(self) -> bool:
        return not self.allowed


@dataclass(frozen=True)
class RouteOption:
    method: str
    target: str
    allowed: bool
    reason: str = ""


class PolicyEngine:
    """Rule-based authorization for commands, routes and relay destinations."""

    def __init__(
        self,
        *,
        allow_destructive_commands: bool = False,
        require_confirmation_for_state_changes: bool = True,
        relay_allowed_destinations: Sequence[str] = (),
        allow_intrusive_tools: bool = False,
    ) -> None:
        self.allow_destructive_commands = allow_destructive_commands
        self.require_confirmation_for_state_changes = require_confirmation_for_state_changes
        self.relay_allowed_destinations = [item.strip() for item in relay_allowed_destinations if item.strip()]
        self.allow_intrusive_tools = allow_intrusive_tools

    # -- command risk -------------------------------------------------------
    @staticmethod
    def classify(command: str, environment: str = "unknown") -> RiskLevel:
        text = (command or "").strip()
        if not text:
            return RiskLevel.UNKNOWN
        for pattern in DESTRUCTIVE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return RiskLevel.DESTRUCTIVE
        for pattern in STATE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return RiskLevel.MODIFIES_STATE
        lowered = text.lower()
        for hint in _READ_ONLY_HINTS:
            if lowered.startswith(hint.lower()) or f"\n{hint.lower()}" in lowered:
                return RiskLevel.READ_ONLY
        return RiskLevel.UNKNOWN

    def evaluate(
        self,
        command: str,
        environment: str = "unknown",
        *,
        approved: bool = False,
        production: bool = False,
    ) -> ApprovalDecision:
        risk = self.classify(command, environment)
        if risk is RiskLevel.DESTRUCTIVE:
            if self.allow_destructive_commands and approved:
                return ApprovalDecision(
                    allowed=True,
                    risk=risk,
                    requires_confirmation=True,
                    reason="destructive command allowed because it was explicitly approved",
                    environment=environment,
                )
            return ApprovalDecision(
                allowed=False,
                risk=risk,
                requires_confirmation=True,
                reason=(
                    "this command can destroy data or disrupt the host; destructive commands are "
                    "blocked unless the operator explicitly enables and approves them"
                ),
                environment=environment,
            )
        if risk is RiskLevel.MODIFIES_STATE:
            if self.require_confirmation_for_state_changes and not approved:
                return ApprovalDecision(
                    allowed=False,
                    risk=risk,
                    requires_confirmation=True,
                    reason="this command changes remote state; confirmation is required",
                    environment=environment,
                )
            if production and not approved:
                return ApprovalDecision(
                    allowed=False,
                    risk=risk,
                    requires_confirmation=True,
                    reason="state-changing command on a production host requires confirmation",
                    environment=environment,
                )
        return ApprovalDecision(
            allowed=True,
            risk=risk,
            requires_confirmation=False,
            reason="no policy restriction applies",
            environment=environment,
        )

    # -- routing ------------------------------------------------------------
    @staticmethod
    def allowed_routes(device: Device) -> list[RouteOption]:
        """Expand a device's configured routing into usable route options.

        Only routes the operator configured are offered; nothing is attempted
        implicitly. ``direct`` is always offered first because it is the least
        privileged path.
        """
        routing: RoutingPreferences = device.routing
        options: list[RouteOption] = [RouteOption("direct", device.hostname, True, "direct connection to the device")]
        # Operator preference first, then every remaining method so the UI and
        # failure reports can explain why an unconfigured route is unavailable.
        ordered: list[str] = []
        for method in (routing.preferred_method, *routing.fallbacks):
            if method and method != "direct" and method not in ordered:
                ordered.append(method)
        for method in ROUTE_ORDER:
            if method != "direct" and method not in ordered:
                ordered.append(method)
        for method in ordered:
            target, allowed, reason = PolicyEngine._route_target(device, method)
            options.append(RouteOption(method, target, allowed, reason))
        return options

    @staticmethod
    def _route_target(device: Device, method: str) -> tuple[str, bool, str]:
        routing = device.routing
        if method == "vpn":
            return ("existing VPN route", True, "uses the VPN already configured in Windows")
        if method == "jump_host":
            if routing.jump_host:
                return (routing.jump_host, True, "jump host configured for this device")
            return ("", False, "no jump host is configured for this device")
        if method == "rdp_gateway":
            if routing.gateway:
                return (routing.gateway, True, "RDP gateway configured for this device")
            return ("", False, "no RDP gateway is configured for this device")
        if method == "cloud_private":
            return ("cloud private network", False, "cloud private routes require a provider adapter (phase 9)")
        if method == "agent":
            if routing.agent_id:
                return (routing.agent_id, True, "registered endpoint agent configured for this device")
            return ("", False, "no endpoint agent is registered for this device")
        if method == "relay":
            if routing.relay:
                return (routing.relay, True, "secure relay configured for this device")
            return ("", False, "no relay is configured for this device")
        return ("", False, f"unsupported route method {method!r}")

    # -- relay / destination restrictions -----------------------------------
    def destination_allowed(self, host: str, port: int | None = None) -> tuple[bool, str]:
        """Relay destinations must be explicitly allow-listed."""
        if not self.relay_allowed_destinations:
            return False, "no relay destinations are allow-listed; relay access is denied by default"
        for entry in self.relay_allowed_destinations:
            allowed_host, _, allowed_port = entry.partition(":")
            if _host_matches(host, allowed_host.strip()) and (
                not allowed_port or port is None or int(allowed_port) == int(port)
            ):
                return True, f"{host}:{port or '*'} matches allow-list entry {entry!r}"
        return False, f"{host}:{port or '*'} is not in the relay allow-list"

    def intrusive_tools_allowed(self, requested: bool) -> tuple[bool, str]:
        if requested and not self.allow_intrusive_tools:
            return False, "credential attacks and vulnerability scripts are disabled in settings"
        return True, "allowed"


def _host_matches(host: str, allowed: str) -> bool:
    if not allowed or allowed == "*":
        return True
    if allowed.startswith("*."):
        suffix = allowed[1:]
        return host.endswith(suffix) or host == allowed[2:]
    if is_ip_literal(host) and "/" in allowed:
        try:
            return ipaddress.ip_address(host) in ipaddress.ip_network(allowed, strict=False)
        except ValueError:
            return False
    return host.casefold() == allowed.casefold()


def classify_command(command: str, environment: str = "unknown") -> RiskLevel:
    """Convenience wrapper used by the assistant and the UI."""
    return PolicyEngine.classify(command, environment)


def summarize_routes(options: Iterable[RouteOption]) -> str:
    lines = []
    for option in options:
        state = "configured" if option.allowed else "unavailable"
        target = option.target or "-"
        lines.append(f"{option.method}: {state} ({target}) - {option.reason}")
    return "\n".join(lines)
