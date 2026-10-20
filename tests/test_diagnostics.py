"""Diagnostics: parsing, routing lookups, TCP results and report wording."""

from __future__ import annotations

import socket
import threading


from nexus_remote.diagnostics import dns, routing, tcp
from nexus_remote.diagnostics.reports import DiagnosticsRunner
from nexus_remote.diagnostics.types import STATUS_OK, STATUS_SKIPPED

ROUTE_PRINT = """
===========================================================================
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.1.1    192.168.1.50     25
        10.10.10.0    255.255.255.0      192.168.1.1    192.168.1.50     26
        10.10.10.7  255.255.255.255         On-link      192.168.1.50    281
      192.168.1.50  255.255.255.255         On-link      192.168.1.50    281
===========================================================================
"""


def test_parse_route_print_rows():
    entries = routing.parse_route_print(ROUTE_PRINT)
    assert len(entries) == 4
    default = entries[0]
    assert default.destination == "0.0.0.0"
    assert default.gateway == "192.168.1.1"
    assert default.prefix_length == 0
    assert entries[1].prefix_length == 24


def test_route_selection_prefers_longest_prefix():
    entries = routing.parse_route_print(ROUTE_PRINT)
    result = routing.route_to("10.10.10.7", entries=entries)
    assert result.ok
    assert "10.10.10.7/32" in result.detail
    assert "10.10.10.7" in result.destination


def test_route_selection_falls_back_to_default():
    entries = routing.parse_route_print(ROUTE_PRINT)
    result = routing.route_to("8.8.8.8", entries=entries)
    assert result.ok
    assert "default route" in result.detail


def test_no_route_is_reported_as_failure():
    entries = routing.parse_route_print(
        "Network Destination Netmask Gateway Interface Metric\n"
        "      10.10.10.0 255.255.255.0 192.168.1.1 192.168.1.50 25\n"
    )
    result = routing.route_to("192.0.2.10", entries=entries)
    assert result.failed
    assert "no route" in result.interpretation.lower()


def test_route_lookup_needs_an_address():
    result = routing.route_to("example.com")
    assert result.result == STATUS_SKIPPED


def test_tcp_connection_succeeds_to_listening_socket():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        result = tcp.tcp_check("127.0.0.1", port, timeout=2.0)
        assert result.ok
        assert "handshake" in result.interpretation.lower()
    finally:
        server.close()


def test_tcp_connection_refused_is_reported(monkeypatch):
    class RefusingSocket:
        def settimeout(self, _timeout):
            pass

        def connect(self, _address):
            raise ConnectionRefusedError(10061, "Connection refused")

        def close(self):
            pass

    monkeypatch.setattr(tcp.socket, "socket", lambda *_args, **_kwargs: RefusingSocket())
    result = tcp.tcp_check("127.0.0.1", 65000, timeout=2.0)
    assert result.failed
    assert "refused" in result.error.lower()
    assert "path exists" in result.interpretation


def test_timeout_interpretation_does_not_assert_a_cause():
    result = tcp.tcp_check("203.0.113.1", 9, timeout=0.25)
    assert result.failed
    assert "cannot distinguish" in result.interpretation


def test_protocol_probe_identifies_ssh_banner():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        connection, _ = server.accept()
        connection.sendall(b"SSH-2.0-OpenSSH_9.6p1 Ubuntu\r\n")
        connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = tcp.protocol_probe("127.0.0.1", port, timeout=2.0)
        assert result.ok
        assert "SSH" in result.interpretation
    finally:
        thread.join(timeout=2)
        server.close()


def test_dns_resolves_localhost():
    result = dns.resolve("localhost")
    assert result.ok
    assert result.evidence["addresses"]


def test_dns_failure_explains_scope(monkeypatch):
    def boom(*_args, **_kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    result = dns.resolve("missing.invalid")
    assert result.failed
    assert "resolver" in result.interpretation
    assert result.next_step


def test_local_dns_servers_returns_list():
    servers = dns.local_dns_servers()
    assert isinstance(servers, list)


def test_runner_skips_tcp_without_a_port():
    report = DiagnosticsRunner(timeout=0.5).run("127.0.0.1")
    tcp_results = [result for result in report.results if result.test == "TCP connectivity"]
    assert tcp_results and tcp_results[0].result == STATUS_SKIPPED


def test_runner_reports_failure_with_next_steps():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    report = DiagnosticsRunner(timeout=1.0).run("127.0.0.1", port)
    assert report.failed
    assert report.summary
    assert report.next_steps
    text = report.render_text()
    assert "SUMMARY" in text
    assert "RECOMMENDED NEXT STEPS" in text


def test_runner_resolves_hostname_first():
    report = DiagnosticsRunner(timeout=1.0).run("localhost", None)
    assert report.results[0].test == "DNS resolution"
    assert report.results[0].ok


def test_ping_is_advisory(monkeypatch):
    monkeypatch.setattr(
        routing,
        "_run",
        lambda *_args, **_kwargs: "Pinging 10.0.0.1 with 32 bytes of data:\nRequest timed out.\n"
        "Packets: Sent = 2, Received = 0, Lost = 2 (100% loss),",
    )
    result = routing.ping("10.0.0.1")
    assert result.failed is False
    assert result.result != STATUS_OK
    assert "not proof" in result.interpretation
