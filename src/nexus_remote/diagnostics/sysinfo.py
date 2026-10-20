"""Remote system information for the connected device.

The collector runs one fixed, read-only POSIX command over an existing SSH
session and parses the result into a small :class:`SystemInfo` value object.
Nothing here executes operator-supplied input: the command is a constant, and
every value is only displayed (never used to build another command).

Parsing is deliberately tolerant. Minimal containers lack ``hostname``,
``nproc`` or ``/etc/os-release``; whatever is present is shown and the rest is
left blank instead of failing the whole collection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Single read-only probe. Section markers keep parsing independent of the
#: individual commands being present (each line that fails is simply empty).
SYSINFO_COMMAND = (
    "echo @@host; hostname 2>/dev/null; "
    "echo @@uname; uname -srm 2>/dev/null; "
    "echo @@os; cat /etc/os-release 2>/dev/null; "
    "echo @@cpu; grep -m1 -E 'model name|^Model|^Hardware' /proc/cpuinfo 2>/dev/null; "
    "nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null; "
    "echo @@mem; cat /proc/meminfo 2>/dev/null; "
    "echo @@disk; df -Pk / 2>/dev/null; "
    "echo @@uptime; cat /proc/uptime 2>/dev/null; "
    "echo @@load; cat /proc/loadavg 2>/dev/null; "
    "echo @@users; who 2>/dev/null | wc -l; "
    "echo @@end"
)

_SECTIONS = (
    "host",
    "uname",
    "os",
    "cpu",
    "mem",
    "disk",
    "uptime",
    "load",
    "users",
)

_MARKERS = {f"@@{name}": name for name in _SECTIONS}


class SystemInfoUnavailable(RuntimeError):
    """The host did not answer with the expected POSIX probe output."""


@dataclass(frozen=True)
class SystemInfo:
    """A snapshot of the remote host's resources.

    Sizes are bytes, durations are seconds. Missing values are ``None`` so the
    UI can distinguish "unknown" from "zero".
    """

    hostname: str = ""
    os_name: str = ""
    kernel: str = ""
    architecture: str = ""
    cpu_model: str = ""
    cpu_cores: int | None = None
    memory_total: int | None = None
    memory_used: int | None = None
    swap_total: int | None = None
    swap_used: int | None = None
    disk_total: int | None = None
    disk_used: int | None = None
    disk_mount: str = "/"
    uptime_seconds: float | None = None
    load_1: float | None = None
    load_5: float | None = None
    load_15: float | None = None
    logged_in_users: int | None = None
    collected_at: str = ""

    @property
    def available(self) -> bool:
        return bool(
            self.hostname
            or self.os_name
            or self.kernel
            or self.cpu_model
            or self.memory_total
            or self.disk_total
        )

    @property
    def memory_percent(self) -> float | None:
        if not self.memory_total or self.memory_used is None:
            return None
        return min(100.0, max(0.0, self.memory_used / self.memory_total * 100))

    @property
    def disk_percent(self) -> float | None:
        if not self.disk_total or self.disk_used is None:
            return None
        return min(100.0, max(0.0, self.disk_used / self.disk_total * 100))

    @property
    def swap_total_or_zero(self) -> int:
        return self.swap_total or 0

    @property
    def swap_used_or_zero(self) -> int:
        return self.swap_used or 0


def collect_system_info(adapter: object, *, timeout: float = 15.0) -> SystemInfo:
    """Run the fixed probe through a connected adapter and parse the result.

    ``adapter`` is a protocol adapter exposing ``execute(command, timeout)``.
    Callers run this on a worker thread: the call blocks on the network.
    """
    execute = getattr(adapter, "execute", None)
    if execute is None:
        raise SystemInfoUnavailable("this session cannot run commands")
    result = execute(SYSINFO_COMMAND, timeout=timeout)
    text = getattr(result, "stdout", "") or ""
    return parse_system_info(text)


def parse_system_info(text: str) -> SystemInfo:
    """Parse the sectioned output of :data:`SYSINFO_COMMAND`."""
    sections = _split_sections(text or "")
    if not sections:
        raise SystemInfoUnavailable(
            "the host did not return POSIX system information (Windows hosts are not supported yet)"
        )

    host = _first_line(sections.get("host", []))
    kernel, architecture = _parse_uname(_first_line(sections.get("uname", [])))
    os_name = _parse_os_release(sections.get("os", []))
    cpu_model, cpu_cores = _parse_cpu(sections.get("cpu", []))
    memory_total, memory_used, swap_total, swap_used = _parse_memory(sections.get("mem", []))
    disk_total, disk_used, disk_mount = _parse_disk(sections.get("disk", []))
    uptime_seconds = _parse_uptime(_first_line(sections.get("uptime", [])))
    load_1, load_5, load_15 = _parse_load(_first_line(sections.get("load", [])))
    users = _parse_count(_first_line(sections.get("users", [])))

    info = SystemInfo(
        hostname=host,
        os_name=os_name,
        kernel=kernel,
        architecture=architecture,
        cpu_model=cpu_model,
        cpu_cores=cpu_cores,
        memory_total=memory_total,
        memory_used=memory_used,
        swap_total=swap_total,
        swap_used=swap_used,
        disk_total=disk_total,
        disk_used=disk_used,
        disk_mount=disk_mount or "/",
        uptime_seconds=uptime_seconds,
        load_1=load_1,
        load_5=load_5,
        load_15=load_15,
        logged_in_users=users,
    )
    if not info.available:
        raise SystemInfoUnavailable("the host returned no recognisable system information")
    return info


# -- formatting -------------------------------------------------------------
def format_bytes(value: int | float | None, *, precision: int = 1) -> str:
    if value is None:
        return "-"
    size = float(max(value, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{size:.0f} B"
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    return f"{size:.{precision}f} TB"


def format_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = int(max(seconds, 0))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}%"


def format_load(info: SystemInfo) -> str:
    values = (info.load_1, info.load_5, info.load_15)
    if all(value is None for value in values):
        return "-"
    return " / ".join("-" if value is None else f"{value:.2f}" for value in values)


# -- parsing helpers --------------------------------------------------------
def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        marker = _MARKERS.get(line.strip())
        if marker:
            current = sections.setdefault(marker, [])
            continue
        if current is not None:
            current.append(line)
    return sections


def _first_line(lines: list[str]) -> str:
    for line in lines:
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return ""


def _parse_uname(line: str) -> tuple[str, str]:
    parts = line.split()
    if not parts:
        return "", ""
    kernel = parts[0]
    architecture = parts[-1] if len(parts) > 1 else ""
    return kernel, architecture


def _parse_os_release(lines: list[str]) -> str:
    values: dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    pretty = values.get("PRETTY_NAME", "")
    if pretty:
        return pretty
    name = values.get("NAME", "")
    version = values.get("VERSION_ID", "") or values.get("VERSION", "")
    if name and version and version not in name:
        return f"{name} {version}"
    return name or version


def _parse_cpu(lines: list[str]) -> tuple[str, int | None]:
    model = ""
    cores: int | None = None
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        if ":" in cleaned:
            key, _, value = cleaned.partition(":")
            if key.strip() in {"model name", "Model", "Hardware"} and not model:
                model = value.strip()
            continue
        if cores is None:
            number = _parse_count(cleaned)
            if number is not None:
                cores = number
    return model, cores


def _parse_memory(lines: list[str]) -> tuple[int | None, int | None, int | None, int | None]:
    values: dict[str, int] = {}
    for line in lines:
        key, _, rest = line.partition(":")
        if not rest:
            continue
        match = re.match(r"\s*(\d+)", rest)
        if match:
            values[key.strip()] = int(match.group(1)) * 1024
    total = values.get("MemTotal")
    if total is None:
        return None, None, None, None
    available = values.get("MemAvailable")
    if available is None:
        free = values.get("MemFree", 0)
        buffers = values.get("Buffers", 0)
        cached = values.get("Cached", 0)
        available = free + buffers + cached
    used = max(total - available, 0)
    swap_total = values.get("SwapTotal") or 0
    swap_free = values.get("SwapFree") or 0
    swap_used = max(swap_total - swap_free, 0) if swap_total else 0
    return total, used, swap_total, swap_used


def _parse_disk(lines: list[str]) -> tuple[int | None, int | None, str]:
    for line in reversed(lines):
        fields = line.split()
        if len(fields) < 6 or not fields[1].isdigit() or not fields[2].isdigit():
            continue
        total = int(fields[1]) * 1024
        used = int(fields[2]) * 1024
        mount = " ".join(fields[5:]) or "/"
        return total, used, mount
    return None, None, "/"


def _parse_uptime(line: str) -> float | None:
    try:
        return float(line.split()[0])
    except (IndexError, ValueError):
        return None


def _parse_load(line: str) -> tuple[float | None, float | None, float | None]:
    parts = line.split()
    if len(parts) < 3:
        return None, None, None
    values: list[float | None] = []
    for part in parts[:3]:
        try:
            values.append(float(part))
        except ValueError:
            values.append(None)
    return values[0], values[1], values[2]


def _parse_count(line: str) -> int | None:
    match = re.search(r"\d+", line)
    return int(match.group(0)) if match else None
