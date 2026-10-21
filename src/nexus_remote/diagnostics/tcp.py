"""TCP connectivity and protocol negotiation diagnostics.

A timeout proves only that nothing answered. It cannot prove which firewall,
router or ACL dropped the packet, and the wording of every result reflects that.
"""

from __future__ import annotations

import socket
import time

from nexus_remote.diagnostics.types import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PARTIAL,
    DiagnosticResult,
)

_DEFAULT_PORT_HINTS = {
    22: "SSH/SFTP",
    23: "Telnet",
    80: "HTTP",
    135: "RPC",
    139: "SMB (NetBIOS)",
    443: "HTTPS",
    445: "SMB",
    3389: "RDP",
    5985: "WinRM (HTTP)",
    5986: "WinRM (HTTPS)",
    5900: "VNC",
}


def tcp_check(host: str, port: int, timeout: float = 5.0) -> DiagnosticResult:
    """Open a TCP connection to ``host:port`` and classify the outcome."""
    destination = f"{host}:{port}"
    service = _DEFAULT_PORT_HINTS.get(port, "")
    label = f"TCP connectivity{' (' + service + ')' if service else ''}"
    started = time.perf_counter()
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
    except TimeoutError:
        elapsed = (time.perf_counter() - started) * 1000
        return DiagnosticResult(
            test=label,
            destination=destination,
            result=STATUS_FAILED,
            error=f"connection timed out after {timeout:g}s",
            interpretation=(
                "no response was received before the timeout. The packet may have been dropped by "
                "a firewall, a route may be missing, or the service may be unreachable from this "
                "network - this test cannot distinguish between them"
            ),
            next_step=(
                "verify the local route to the destination, confirm any required VPN or jump host "
                "is connected, and check firewall policy for this source and port"
            ),
            duration_ms=elapsed,
        )
    except ConnectionRefusedError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return DiagnosticResult(
            test=label,
            destination=destination,
            result=STATUS_FAILED,
            error=f"connection refused ({exc.errno})",
            interpretation=(
                "the host answered with a TCP reset, so a path exists and something is listening "
                "on the host - but not on this port"
            ),
            next_step="check the service is running and listening on this port, or try the correct port",
            duration_ms=elapsed,
        )
    except OSError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return DiagnosticResult(
            test=label,
            destination=destination,
            result=STATUS_FAILED,
            error=f"{exc.strerror or exc} (errno {exc.errno})",
            interpretation="the connection attempt failed before an application response was seen",
            next_step="confirm the address is correct and reachable from this network",
            duration_ms=elapsed,
        )
    else:
        elapsed = (time.perf_counter() - started) * 1000
        peer = ""
        try:
            peer = f"{sock.getpeername()[0]}:{sock.getpeername()[1]}"
        except OSError:  # pragma: no cover - defensive
            peer = destination
        return DiagnosticResult(
            test=label,
            destination=destination,
            result=STATUS_OK,
            detail=f"TCP handshake completed to {peer} in {elapsed:.0f} ms",
            interpretation=(
                "a full TCP handshake completed, so a path exists and something is listening on "
                "this port. This does not yet prove the service is the one you expect"
            ),
            next_step="run the protocol probe to confirm the service identity",
            duration_ms=elapsed,
            evidence={"peer": peer},
        )
    finally:
        sock.close()


def protocol_probe(host: str, port: int, timeout: float = 5.0, *, max_bytes: int = 512) -> DiagnosticResult:
    """Read the first bytes a service sends and try to identify the protocol."""
    destination = f"{host}:{port}"
    started = time.perf_counter()
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        try:
            banner = sock.recv(max_bytes)
        except TimeoutError:
            return DiagnosticResult(
                test="Protocol probe",
                destination=destination,
                result=STATUS_PARTIAL,
                detail="connected, but the service sent nothing unprompted",
                interpretation=(
                    "the port is open. Services such as RDP, SMB and HTTPS wait for the client to "
                    "speak first, so silence is expected for them"
                ),
                next_step="use the protocol-specific client to complete the handshake",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
    except (TimeoutError, OSError) as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return DiagnosticResult(
            test="Protocol probe",
            destination=destination,
            result=STATUS_FAILED,
            error=f"{exc}",
            interpretation="the probe could not complete because the TCP connection did not succeed",
            next_step="resolve the TCP connectivity result first",
            duration_ms=elapsed,
        )
    finally:
        sock.close()

    elapsed = (time.perf_counter() - started) * 1000
    text = banner.decode("utf-8", errors="replace")
    identified = _identify_banner(text)
    if identified:
        return DiagnosticResult(
            test="Protocol probe",
            destination=destination,
            result=STATUS_OK,
            detail=f"banner: {text.strip()[:120]}",
            interpretation=f"the service identifies itself as {identified}",
            next_step="connect with the matching client from the device profile",
            duration_ms=elapsed,
            evidence={"banner": text.strip(), "protocol": identified},
        )
    return DiagnosticResult(
        test="Protocol probe",
        destination=destination,
        result=STATUS_PARTIAL,
        detail=f"unrecognised banner: {text.strip()[:120]}",
        interpretation=(
            "the port is open and returned data, but the response does not match a known protocol. "
            "Treat remote banners as untrusted text"
        ),
        next_step="verify the port mapping with the service owner before connecting",
        duration_ms=elapsed,
        evidence={"banner": text.strip()},
    )


def _identify_banner(text: str) -> str | None:
    cleaned = text.strip()
    if cleaned.upper().startswith("SSH-"):
        parts = cleaned.split("-")
        version = parts[2] if len(parts) > 2 else ""
        return f"SSH ({cleaned.split()[-1] or version})"
    if cleaned.upper().startswith("HTTP/"):
        return "HTTP"
    if cleaned.startswith("220") and "FTP" in cleaned.upper():
        return "FTP"
    if cleaned.startswith("220") and "SMTP" in cleaned.upper():
        return "SMTP"
    if cleaned.startswith("* OK"):
        return "IMAP"
    return None
