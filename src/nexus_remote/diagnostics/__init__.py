"""Network diagnostics engine (works fully offline)."""

from nexus_remote.diagnostics.dns import local_dns_servers, resolve, reverse_lookup
from nexus_remote.diagnostics.reports import DiagnosticReport, DiagnosticsRunner
from nexus_remote.diagnostics.routing import (
    default_gateways,
    interfaces,
    parse_route_print,
    ping,
    route_to,
    vpn_adapters,
)
from nexus_remote.diagnostics.tcp import protocol_probe, tcp_check
from nexus_remote.diagnostics.types import DiagnosticResult

__all__ = [
    "DiagnosticReport",
    "DiagnosticResult",
    "DiagnosticsRunner",
    "default_gateways",
    "interfaces",
    "local_dns_servers",
    "parse_route_print",
    "ping",
    "protocol_probe",
    "resolve",
    "reverse_lookup",
    "route_to",
    "tcp_check",
    "vpn_adapters",
]
