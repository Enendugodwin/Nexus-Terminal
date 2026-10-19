"""Local routing, interface and reachability diagnostics.

This module inspects *this* machine's configuration. It never claims to know
the remote side's routes, firewalls or ACLs.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from dataclasses import dataclass

from nexus_remote.diagnostics.types import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_SKIPPED,
    DiagnosticResult,
)

_ROW_RE = re.compile(
    r"^\s*((?:\d{1,3}\.){3}\d{1,3})\s+((?:\d{1,3}\.){3}\d{1,3})\s+"
    r"((?:\d{1,3}\.){3}\d{1,3}|On-link)\s+((?:\d{1,3}\.){3}\d{1,3})\s+(\d+)\s*$",
    re.IGNORECASE,
)

_VPN_HINTS = (
    "vpn", "tap", "tun", "wireguard", "wg", "anyconnect", "openvpn", "forticlient",
    "fortinet", "globalprotect", "checkpoint", "zerotier", "tailscale", "nordlynx",
)


@dataclass(frozen=True)
class RouteEntry:
    destination: str
    netmask: str
    gateway: str
    interface: str
    metric: int

    @property
    def prefix_length(self) -> int:
        try:
            return ipaddress.ip_network(f"{self.destination}/{self.netmask}", strict=False).prefixlen
        except ValueError:
            return 0

    @property
    def network(self) -> ipaddress.IPv4Network | None:
        try:
            return ipaddress.ip_network(f"{self.destination}/{self.netmask}", strict=False)
        except ValueError:
            return None


def parse_route_print(text: str) -> list[RouteEntry]:
    """Parse ``route print`` output (language independent for numeric rows)."""
    entries: list[RouteEntry] = []
    for line in text.splitlines():
        match = _ROW_RE.match(line)
        if not match:
            continue
        destination, netmask, gateway, interface, metric = match.groups()
        entries.append(
            RouteEntry(
                destination=destination,
                netmask=netmask,
                gateway=gateway,
                interface=interface,
                metric=int(metric),
            )
        )
    return entries


def _run(command: list[str], timeout: float = 15.0) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout or ""


def route_table() -> list[RouteEntry]:
    """Return the IPv4 routing table of this machine."""
    if os.name == "nt":
        return parse_route_print(_run(["route", "print", "-4"]))
    output = _run(["ip", "route"])
    entries: list[RouteEntry] = []
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        network = parts[0]
        gateway = parts[2] if len(parts) > 2 and parts[1] == "via" else "0.0.0.0"
        interface = parts[-1]
        if "/" in network:
            address, _, prefix = network.partition("/")
            mask = str(ipaddress.ip_network(f"0.0.0.0/{prefix}", strict=False).netmask)
        else:
            address, mask = network, "0.0.0.0"
        entries.append(RouteEntry(address, mask, gateway, interface, 0))
    return entries


def default_gateways() -> list[RouteEntry]:
    return [entry for entry in route_table() if entry.destination == "0.0.0.0" and entry.netmask == "0.0.0.0"]


def route_to(host: str, entries: list[RouteEntry] | None = None) -> DiagnosticResult:
    """Report which local route would be used to reach ``host``."""
    if not _is_ip(host):
        return DiagnosticResult(
            test="Local route lookup",
            destination=host,
            result=STATUS_SKIPPED,
            detail="hostname was not resolved to an address first",
            interpretation="routing decisions are made on IP addresses, not names",
            next_step="resolve the hostname first, or run the check by IP address",
        )
    table = entries if entries is not None else route_table()
    if not table:
        return DiagnosticResult(
            test="Local route lookup",
            destination=host,
            result=STATUS_SKIPPED,
            detail="the local routing table could not be read",
            interpretation="no conclusion can be drawn about routing",
            next_step="run 'route print' (Windows) or 'ip route' manually",
        )
    address = ipaddress.ip_address(host)
    best: RouteEntry | None = None
    for entry in table:
        network = entry.network
        if network is None or address not in network:
            continue
        if best is None or entry.prefix_length > best.prefix_length:
            best = entry
    if best is None:
        return DiagnosticResult(
            test="Local route lookup",
            destination=host,
            result=STATUS_FAILED,
            detail="no route in the local table matches this address",
            interpretation=(
                "this machine has no route covering the destination, so packets cannot leave - "
                "typically a missing VPN connection or an absent network route"
            ),
            next_step="connect the required VPN or add the route, then retest",
        )
    kind = "default route" if best.prefix_length == 0 else f"{best.destination}/{best.prefix_length} route"
    via_vpn = any(hint in best.interface.casefold() for hint in _VPN_HINTS)
    return DiagnosticResult(
        test="Local route lookup",
        destination=host,
        result=STATUS_OK,
        detail=(
            f"{kind} via gateway {best.gateway} out of interface {best.interface} "
            f"(metric {best.metric})"
        ),
        interpretation=(
            "the local host has a route to the destination"
            + (" through a VPN interface" if via_vpn else "")
            + ". Intermediate firewalls and the remote host's own routing are not visible here"
        ),
        next_step="test TCP connectivity to the port you need",
        evidence={"gateway": best.gateway, "interface": best.interface, "prefix_length": best.prefix_length},
    )


def interfaces() -> list[dict[str, str]]:
    """Return a summarised view of local network interfaces."""
    if os.name != "nt":
        return _posix_interfaces()
    output = _run(["ipconfig", "/all"], timeout=20)
    blocks = [block for block in re.split(r"\r?\n\r?\n", output) if block.strip()]
    result: list[dict[str, str]] = []
    for block in blocks:
        first = block.splitlines()[0].strip()
        if not first or "Windows IP" in first or "Konfiguration" in first:
            continue
        if not first.endswith(":") or len(first) > 120:
            continue
        name = first.rstrip(":")
        name = re.sub(r"(?i)^.*?(adapter|adapter\b)\s*", "", name).strip() or name
        info: dict[str, str] = {"name": name}
        patterns = {
            "ipv4": r"(?i)(?:IPv4 Address|IPv4-Adresse|Adresse IPv4|IPv4 \S+)[^:]*:\s*((?:\d{1,3}\.){3}\d{1,3})(?:\s*\(Preferred\))?",
            "mask": r"(?i)(?:Subnet Mask|Subnetzmaske|Masque de sous-réseau)[^:]*:\s*((?:\d{1,3}\.){3}\d{1,3})",
            "gateway": r"(?i)(?:Default Gateway|Standardgateway|Passerelle par défaut)[^:]*:\s*((?:\d{1,3}\.){3}\d{1,3})",
            "mac": r"(?i)(?:Physical Address|Physikalische Adresse|Adresse physique)[^:]*:\s*([0-9A-Fa-f\-]{11,})",
            "ipv6": r"(?i)(?:IPv6 Address|IPv6-Adresse)[^:]*:\s*([0-9a-fA-F:]{6,})(?:\s*\(\S+\))?",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, block)
            if match:
                info[key] = match.group(1).strip()
        if "ipv4" in info or "ipv6" in info:
            result.append(info)
    return result


def _posix_interfaces() -> list[dict[str, str]]:
    output = _run(["ip", "-brief", "address"])
    result: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        name = parts[0]
        info = {"name": name, "state": parts[1]}
        for address in parts[3:]:
            if ":" not in address:
                info.setdefault("ipv4", address)
            else:
                info.setdefault("ipv6", address)
        result.append(info)
    return result


def vpn_adapters() -> list[dict[str, str]]:
    """Return interface entries that look like VPN/tunnel adapters."""
    found: list[dict[str, str]] = []
    for info in interfaces():
        name = info.get("name", "").casefold()
        if any(hint in name for hint in _VPN_HINTS):
            found.append(info)
    return found


def ping(host: str, *, count: int = 2, timeout_ms: int = 1500) -> DiagnosticResult:
    """ICMP reachability test with explicit caveats about ICMP filtering."""
    if os.name == "nt":
        command = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
    else:
        command = ["ping", "-c", str(count), "-W", str(max(1, timeout_ms // 1000)), host]
    output = _run(command, timeout=max(5.0, count * (timeout_ms / 1000) + 3))
    if not output:
        return DiagnosticResult(
            test="ICMP ping",
            destination=host,
            result=STATUS_SKIPPED,
            detail="the ping command is unavailable or failed to run",
            interpretation="nobody can conclude anything from a missing tool",
            next_step="rely on the TCP connectivity test instead",
        )
    lost_match = re.search(r"(?i)(\d+)%\s*(?:loss|Verlust|perte)", output)
    if lost_match is None:
        lost_match = re.search(r"(?i)(\d+)%\s*packet", output)
    loss = int(lost_match.group(1)) if lost_match else None
    times = [float(value) for value in re.findall(r"(?i)time[=<]\s*(\d+(?:\.\d+)?)\s*ms", output)]
    reached = loss is not None and loss < 100
    if reached:
        return DiagnosticResult(
            test="ICMP ping",
            destination=host,
            result=STATUS_OK if loss == 0 else STATUS_PARTIAL,
            detail=f"{100 - loss}% of pings answered"
            + (f", round trip {min(times):.0f}-{max(times):.0f} ms" if times else ""),
            interpretation="the host (or a device on its path) answered ICMP",
            next_step="test the actual service port; ICMP success does not prove the service is listening",
            evidence={"loss_percent": loss, "times_ms": times},
        )
    return DiagnosticResult(
        test="ICMP ping",
        destination=host,
        result=STATUS_PARTIAL,
        detail="no ICMP echo reply was received",
        interpretation=(
            "no ICMP reply is not proof that the device is offline - many hosts and firewalls drop "
            "ICMP while their services remain fully reachable over TCP"
        ),
        next_step="test the service port with the TCP check before concluding the device is down",
        evidence={"loss_percent": loss},
    )


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
