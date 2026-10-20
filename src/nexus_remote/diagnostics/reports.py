"""Composes individual checks into a structured diagnostic report."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from nexus_remote.diagnostics import dns as dns_checks
from nexus_remote.diagnostics import routing as routing_checks
from nexus_remote.diagnostics import tcp as tcp_checks
from nexus_remote.diagnostics.types import (
    STATUS_SKIPPED,
    DiagnosticResult,
)
from nexus_remote.util import utcnow_iso

#: Protocol -> default port, used when a report is run for a device profile.
PROTOCOL_PORTS = {
    "ssh": 22,
    "sftp": 22,
    "rdp": 3389,
    "smb": 445,
    "vnc": 5900,
    "winrm": 5985,
    "https": 443,
}


@dataclass
class DiagnosticReport:
    """The result of running the diagnostic chain for one destination."""

    target: str
    port: int | None = None
    protocol: str | None = None
    started_at: str = field(default_factory=utcnow_iso)
    results: list[DiagnosticResult] = field(default_factory=list)
    summary: str = ""
    next_steps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(result.ok or result.result == STATUS_SKIPPED for result in self.results)

    @property
    def failed(self) -> list[DiagnosticResult]:
        return [result for result in self.results if result.failed]

    def add(self, result: DiagnosticResult) -> DiagnosticResult:
        self.results.append(result)
        return result

    def first_failure(self) -> DiagnosticResult | None:
        for result in self.results:
            if result.failed:
                return result
        return None

    def render_text(self) -> str:
        lines = [
            "NEXUS REMOTE DIAGNOSTIC REPORT",
            f"Target:    {self.target}" + (f":{self.port}" if self.port else ""),
            f"Protocol:  {self.protocol or 'unspecified'}",
            f"Started:   {self.started_at}",
            "",
        ]
        for result in self.results:
            lines.append(result.render())
            lines.append("")
        if self.summary:
            lines.append(f"SUMMARY: {self.summary}")
        if self.next_steps:
            lines.append("")
            lines.append("RECOMMENDED NEXT STEPS")
            for step in self.next_steps:
                lines.append(f"  - {step}")
        return "\n".join(lines)

    def as_rows(self) -> list[dict[str, str]]:
        return [result.as_row() for result in self.results]


class DiagnosticsRunner:
    """Runs DNS -> local route -> TCP -> protocol probes, in that order."""

    def __init__(self, *, timeout: float = 5.0) -> None:
        self.timeout = timeout

    def run(
        self,
        target: str,
        port: int | None = None,
        *,
        protocol: str | None = None,
        include_reverse: bool = False,
        include_ping: bool = False,
        route_entries: list[routing_checks.RouteEntry] | None = None,
    ) -> DiagnosticReport:
        if port is None and protocol:
            port = PROTOCOL_PORTS.get(protocol)
        report = DiagnosticReport(target=target, port=port, protocol=protocol)

        is_ip = routing_checks._is_ip(target)
        address = target
        if not is_ip:
            resolution = report.add(dns_checks.resolve(target, timeout=self.timeout))
            if resolution.ok:
                address = str(resolution.evidence.get("addresses", [target])[0])
            else:
                report.summary = (
                    "the destination name could not be resolved locally, so connectivity to the "
                    "device cannot be tested yet"
                )
                report.next_steps = [resolution.next_step] if resolution.next_step else []
                return report

        report.add(routing_checks.route_to(address, entries=route_entries))

        if include_ping:
            report.add(routing_checks.ping(target))

        if port:
            tcp_result = report.add(tcp_checks.tcp_check(address, port, timeout=self.timeout))
            if tcp_result.ok:
                report.add(tcp_checks.protocol_probe(address, port, timeout=self.timeout))
        else:
            report.add(
                DiagnosticResult(
                    test="TCP connectivity",
                    destination=target,
                    result=STATUS_SKIPPED,
                    detail="no port was supplied",
                    interpretation="a TCP test needs a port",
                    next_step="select a protocol or provide a port to test",
                )
            )

        if include_reverse and routing_checks._is_ip(address):
            report.add(dns_checks.reverse_lookup(address, timeout=self.timeout))

        report.summary = self._summarize(report)
        report.next_steps = self._next_steps(report)
        return report

    def run_for_device(self, device: object, *, include_ping: bool = False) -> DiagnosticReport:
        """Convenience wrapper for a Device/protocol pair."""
        protocol = getattr(device, "primary_protocol")()
        hostname = getattr(device, "hostname")
        port = None
        cfg = getattr(device, "connection")(protocol) if protocol else None
        if cfg is not None:
            port = cfg.port
        return self.run(hostname, port, protocol=protocol, include_ping=include_ping)

    # -- summaries ----------------------------------------------------------
    @staticmethod
    def _summarize(report: DiagnosticReport) -> str:
        if not report.results:
            return "no checks were run"
        failure = report.first_failure()
        if failure is None:
            reachable = any(result.ok for result in report.results)
            return (
                "all checks that could run succeeded; the destination answered on the tested port"
                if reachable
                else "no failure was detected, but no positive response was obtained either"
            )
        mapping = {
            "DNS resolution": "name resolution failed before any connectivity test could run",
            "Local route lookup": "this machine has no route covering the destination",
            "TCP connectivity": "the service port did not accept a TCP connection",
            "Protocol probe": "the port answered but the service could not be identified",
        }
        return mapping.get(failure.test, f"{failure.test} failed")

    @staticmethod
    def _next_steps(report: DiagnosticReport) -> list[str]:
        steps: list[str] = []
        for result in report.failed:
            if result.next_step and result.next_step not in steps:
                steps.append(result.next_step)
        if not steps:
            for result in report.results:
                if result.next_step and result.next_step not in steps:
                    steps.append(result.next_step)
        return steps[:4]


def render_results(results: Iterable[DiagnosticResult]) -> str:
    return "\n\n".join(result.render() for result in results)
