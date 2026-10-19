"""DNS resolution diagnostics."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time

from nexus_remote.diagnostics.types import STATUS_FAILED, STATUS_OK, DiagnosticResult


def resolve(host: str, timeout: float = 3.0) -> DiagnosticResult:
    """Resolve ``host`` and report the addresses returned."""
    started = time.perf_counter()
    original_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return DiagnosticResult(
            test="DNS resolution",
            destination=host,
            result=STATUS_FAILED,
            error=f"{exc}",
            interpretation=(
                "the local resolver could not turn this name into an address; nothing about the "
                "destination itself can be concluded yet"
            ),
            next_step=(
                "check the spelling of the hostname, try the IP address directly, and verify the "
                "configured DNS servers are reachable"
            ),
            duration_ms=elapsed,
        )
    except OSError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return DiagnosticResult(
            test="DNS resolution",
            destination=host,
            result=STATUS_FAILED,
            error=f"{exc}",
            interpretation="the resolver call failed before an answer was returned",
            next_step="check local DNS configuration and network connectivity",
            duration_ms=elapsed,
        )
    finally:
        socket.setdefaulttimeout(original_timeout)

    elapsed = (time.perf_counter() - started) * 1000
    addresses: list[str] = []
    families: set[str] = set()
    for family, _type, _proto, _canon, sockaddr in infos:
        address = sockaddr[0]
        if address not in addresses:
            addresses.append(address)
            families.add("IPv6" if family == socket.AF_INET6 else "IPv4")
    return DiagnosticResult(
        test="DNS resolution",
        destination=host,
        result=STATUS_OK,
        detail=", ".join(f"{addr} ({'IPv6' if ':' in addr else 'IPv4'})" for addr in addresses),
        interpretation=(
            "the name resolves locally; this does not prove the destination is reachable or that "
            "the resolved address is the one the service listens on"
        ),
        next_step="test TCP connectivity to the port you intend to use",
        duration_ms=elapsed,
        evidence={"addresses": addresses, "families": sorted(families)},
    )


def reverse_lookup(address: str, timeout: float = 3.0) -> DiagnosticResult:
    """Reverse-resolve an IP address (PTR)."""
    started = time.perf_counter()
    original_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        name, _aliases, _addresses = socket.gethostbyaddr(address)
    except (socket.herror, socket.gaierror, OSError) as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return DiagnosticResult(
            test="Reverse DNS",
            destination=address,
            result=STATUS_FAILED,
            error=f"{exc}",
            interpretation=(
                "no PTR record was returned; many production addresses legitimately have none, so "
                "this alone is not a fault"
            ),
            next_step="continue with TCP connectivity tests",
            duration_ms=elapsed,
        )
    finally:
        socket.setdefaulttimeout(original_timeout)
    elapsed = (time.perf_counter() - started) * 1000
    return DiagnosticResult(
        test="Reverse DNS",
        destination=address,
        result=STATUS_OK,
        detail=name,
        interpretation="the address has a PTR record",
        next_step="compare the name against the expected device name",
        duration_ms=elapsed,
        evidence={"name": name},
    )


def local_dns_servers() -> list[str]:
    """Return the DNS servers configured on this machine."""
    servers: list[str] = []
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["ipconfig", "/all"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover - platform specific
            return servers
        pattern = re.compile(
            r"(?i)(DNS Servers|DNS-Server|Serveurs DNS)\s*[.:]?\s*((?:\d{1,3}\.){3}\d{1,3}|[0-9a-f:]{6,})"
        )
        for match in pattern.finditer(completed.stdout):
            server = match.group(2).strip()
            if server not in servers:
                servers.append(server)
        if servers:
            return servers
        # Continuation lines under "DNS Servers" list additional servers.
        lines = completed.stdout.splitlines()
        collecting = False
        for line in lines:
            if re.search(r"(?i)(DNS Servers|DNS-Server|Serveurs DNS)", line):
                collecting = True
                continue
            if collecting:
                stripped = line.strip()
                if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", stripped):
                    if stripped not in servers:
                        servers.append(stripped)
                elif stripped and not stripped[0].isdigit():
                    collecting = False
    else:
        resolv = "/etc/resolv.conf"
        try:
            with open(resolv, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped.startswith("nameserver"):
                        parts = stripped.split()
                        if len(parts) > 1 and parts[1] not in servers:
                            servers.append(parts[1])
        except OSError:
            return servers
    return servers
