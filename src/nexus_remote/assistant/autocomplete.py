"""Shell-aware command autocomplete.

Every suggestion carries its provenance (verified / history / documentation /
inferred / AI-generated) so the operator can judge it. Suggestions never cross
shell boundaries: a Cisco command is not offered in a bash session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

#: Environments the engine understands.
ENVIRONMENTS = ("bash", "powershell", "cmd", "cisco", "panos", "unknown")

_ALIASES = {
    "linux": "bash",
    "unix": "bash",
    "sh": "bash",
    "shell": "bash",
    "ssh": "bash",
    "windows": "powershell",
    "win": "powershell",
    "pwsh": "powershell",
    "ps": "powershell",
    "command prompt": "cmd",
    "dos": "cmd",
    "ios": "cisco",
    "cisco ios": "cisco",
    "pan-os": "panos",
    "paloalto": "panos",
    "palo alto": "panos",
}


class SuggestionSource(str, Enum):
    VERIFIED = "verified"
    HISTORY = "history"
    DOCUMENTATION = "documentation"
    INFERRED = "inferred"
    AI_GENERATED = "ai-generated"

    @property
    def label(self) -> str:
        return {
            SuggestionSource.VERIFIED: "Verified",
            SuggestionSource.HISTORY: "History",
            SuggestionSource.DOCUMENTATION: "Documentation",
            SuggestionSource.INFERRED: "Inferred",
            SuggestionSource.AI_GENERATED: "AI-generated (unverified)",
        }[self]

    @property
    def trusted(self) -> bool:
        return self in {SuggestionSource.VERIFIED, SuggestionSource.DOCUMENTATION}


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    environment: str
    summary: str = ""
    source: SuggestionSource = SuggestionSource.VERIFIED


@dataclass(frozen=True)
class Suggestion:
    text: str
    source: SuggestionSource
    environment: str
    summary: str = ""
    score: float = 0.0

    @property
    def label(self) -> str:
        return self.source.label

    def display(self) -> str:
        suffix = f" - {self.summary}" if self.summary else ""
        return f"{self.text}{suffix}   [{self.label}]"


def normalize_environment(environment: str | None) -> str:
    if not environment:
        return "unknown"
    key = str(environment).strip().lower()
    if key in ENVIRONMENTS:
        return key
    return _ALIASES.get(key, "unknown")


def environment_for_os(operating_system: str | None) -> str:
    """Map a device OS to a default shell environment."""
    value = (operating_system or "").strip().lower()
    if value == "linux":
        return "bash"
    if value == "windows":
        return "powershell"
    if value == "network":
        return "unknown"  # vendor CLI must be declared per device
    return "unknown"


#: Curated command catalog. Entries are environment-specific on purpose.
_CATALOG: dict[str, list[tuple[str, str]]] = {
    "bash": [
        ("ls -la", "list all files with details"),
        ("cd ", "change directory"),
        ("pwd", "print working directory"),
        ("cat ", "show file contents"),
        ("tail -f ", "follow a log file"),
        ("grep -r ", "search recursively"),
        ("df -h", "filesystem usage"),
        ("du -sh *", "directory sizes"),
        ("free -m", "memory usage"),
        ("ps aux", "running processes"),
        ("top", "live process view"),
        ("ss -tulpn", "listening sockets"),
        ("ip addr", "interface addresses"),
        ("ip route", "routing table"),
        ("systemctl status ", "service status"),
        ("journalctl -u ", "service logs"),
        ("uname -a", "kernel information"),
        ("whoami", "current user"),
        ("id", "user and group ids"),
        ("sudo -l", "list permitted sudo commands"),
        ("uptime", "system load and uptime"),
        ("history", "shell history"),
        ("tar -czf ", "create a gzip archive"),
        ("scp ", "copy files over SSH"),
        ("ping -c 4 ", "ICMP reachability"),
        ("dig +short ", "DNS lookup"),
        ("traceroute ", "path discovery"),
        ("nproc", "cpu count"),
        ("mount | column -t", "mounted filesystems"),
        ("lsblk", "block devices"),
    ],
    "powershell": [
        ("Get-Help ", "show help for a command"),
        ("Get-Process", "running processes"),
        ("Get-Service", "service inventory"),
        ("Get-ChildItem", "list directory contents"),
        ("Get-Content ", "read a file"),
        ("Set-Location ", "change directory"),
        ("Test-NetConnection -ComputerName ", "test TCP reachability"),
        ("Resolve-DnsName ", "DNS lookup"),
        ("Get-NetIPConfiguration", "IP configuration"),
        ("Get-NetRoute", "routing table"),
        ("Get-EventLog -LogName System -Newest 50", "recent system events"),
        ("Get-CimInstance Win32_OperatingSystem", "OS details"),
        ("Get-LocalUser", "local accounts"),
        ("Get-SmbShare", "SMB shares"),
        ("Get-Volume", "disk volumes"),
        ("Get-ComputerInfo", "system inventory"),
        ("Invoke-Command -Session ", "run a command in a session"),
        ("Enter-PSSession -ComputerName ", "interactive remote session"),
        ("Copy-Item -ToSession ", "copy files to a session"),
        ("Restart-Service ", "restart a service"),
        ("Stop-Process -Id ", "stop a process"),
        ("Get-WinEvent -LogName Security -MaxEvents 20", "recent security events"),
    ],
    "cmd": [
        ("dir", "list directory contents"),
        ("cd /d ", "change drive and directory"),
        ("type ", "show file contents"),
        ("ipconfig /all", "full IP configuration"),
        ("route print", "routing table"),
        ("netstat -ano", "connections with process ids"),
        ("nslookup ", "DNS lookup"),
        ("ping -n 4 ", "ICMP reachability"),
        ("tracert ", "path discovery"),
        ("systeminfo", "system inventory"),
        ("tasklist", "running processes"),
        ("whoami /all", "current user and groups"),
        ("net use", "mapped network drives"),
        ("net view \\\\", "list SMB shares"),
        ("gpresult /r", "applied group policy"),
        ("sfc /scannow", "system file check"),
        ("shutdown /r /t 0", "restart the machine"),
    ],
    "cisco": [
        ("show version", "IOS version and uptime"),
        ("show running-config", "active configuration"),
        ("show interfaces status", "interface state"),
        ("show ip interface brief", "interface addresses"),
        ("show ip route", "routing table"),
        ("show vlan brief", "VLAN membership"),
        ("show mac address-table", "MAC forwarding table"),
        ("show cdp neighbors detail", "connected Cisco devices"),
        ("show logging", "device log"),
        ("show processes cpu sorted", "cpu usage"),
        ("show environment all", "power and temperature"),
        ("show ip arp", "ARP cache"),
        ("show ntp status", "time synchronisation"),
        ("ping ", "ICMP reachability"),
        ("traceroute ", "path discovery"),
        ("terminal length 0", "disable paging"),
    ],
    "panos": [
        ("show system info", "device information"),
        ("show interface all", "interface overview"),
        ("show routing route", "routing table"),
        ("show session all", "session table"),
        ("show counter global", "global counters"),
        ("show jobs all", "recent jobs"),
        ("show admins", "administrator accounts"),
        ("show config running", "running configuration"),
        ("show log system last 20", "recent system log"),
        ("show log traffic direction eq inbound", "recent inbound sessions"),
        ("show system resources", "cpu and memory"),
        ("test routing fib-lookup virtual-router default ip ", "route lookup"),
        ("ping host ", "ICMP reachability"),
        ("traceroute host ", "path discovery"),
    ],
}


class CompletionEngine:
    """Produces ranked, provenance-labelled suggestions within milliseconds."""

    def __init__(self, catalog: dict[str, Sequence[tuple[str, str]]] | None = None) -> None:
        source = catalog if catalog is not None else _CATALOG
        self._definitions: dict[str, list[CommandDefinition]] = {
            environment: [
                CommandDefinition(name=name, environment=environment, summary=summary)
                for name, summary in entries
            ]
            for environment, entries in source.items()
        }

    # -- queries ------------------------------------------------------------
    def environments(self) -> list[str]:
        return sorted(self._definitions)

    def definitions(self, environment: str) -> list[CommandDefinition]:
        return list(self._definitions.get(normalize_environment(environment), []))

    def suggest(
        self,
        prefix: str,
        environment: str,
        *,
        history: Iterable[str] = (),
        limit: int = 8,
        include_ai: Sequence[Suggestion] = (),
    ) -> list[Suggestion]:
        """Rank suggestions for ``prefix`` in ``environment``.

        History hits boost catalog entries and are also surfaced on their own
        with the ``History`` label.
        """
        env = normalize_environment(environment)
        text = (prefix or "").strip()
        if env == "unknown":
            return []
        definitions = self._definitions.get(env, [])
        history_list = [item.strip() for item in history if item and item.strip()]
        history_counts: dict[str, int] = {}
        for command in history_list:
            history_counts[command] = history_counts.get(command, 0) + 1

        suggestions: dict[str, Suggestion] = {}
        for definition in definitions:
            score = _match_score(text, definition.name)
            if score <= 0:
                continue
            if definition.name in history_counts:
                score += 30
            suggestions[definition.name] = Suggestion(
                text=definition.name,
                source=definition.source,
                environment=env,
                summary=definition.summary,
                score=score,
            )

        for command, count in history_counts.items():
            if not command:
                continue
            score = _match_score(text, command)
            if score <= 0:
                continue
            score += 10 + min(count, 10)
            existing = suggestions.get(command)
            if existing is None or existing.source is not SuggestionSource.VERIFIED:
                suggestions[command] = Suggestion(
                    text=command,
                    source=SuggestionSource.HISTORY,
                    environment=env,
                    summary="previously used on this device",
                    score=score,
                )

        for extra in include_ai:
            if normalize_environment(extra.environment) != env:
                continue
            score = _match_score(text, extra.text)
            if score <= 0:
                continue
            suggestions.setdefault(
                extra.text,
                Suggestion(
                    text=extra.text,
                    source=SuggestionSource.AI_GENERATED,
                    environment=env,
                    summary=extra.summary or "model proposal - verify before running",
                    score=score - 20,
                ),
            )

        ranked = sorted(
            suggestions.values(),
            key=lambda item: (-item.score, item.source is not SuggestionSource.VERIFIED, item.text),
        )
        return ranked[:limit]

    def explain(self, command: str, environment: str) -> str:
        env = normalize_environment(environment)
        base = (command or "").strip().split(" ")[0]
        for definition in self._definitions.get(env, []):
            if definition.name.split(" ")[0] == base:
                return (
                    f"{definition.name}: {definition.summary or 'no summary available'} "
                    f"({definition.source.label} for {env})"
                )
        return (
            f"no built-in description for {base!r} in {env}; treat it as unverified and check the "
            "vendor documentation before running it"
        )

    def timing(self, prefix: str, environment: str) -> float:
        """Return milliseconds taken by one suggestion pass (perf assertion helper)."""
        started = time.perf_counter()
        self.suggest(prefix, environment)
        return (time.perf_counter() - started) * 1000


def _match_score(prefix: str, candidate: str) -> float:
    if not prefix:
        return 1.0
    lowered = candidate.casefold()
    needle = prefix.casefold()
    if lowered.startswith(needle):
        return 100.0 - (len(candidate) - len(prefix)) * 0.1
    if any(word.startswith(needle) for word in lowered.split()):
        return 60.0
    if needle in lowered:
        return 20.0
    return 0.0


def default_engine() -> CompletionEngine:
    return CompletionEngine()
