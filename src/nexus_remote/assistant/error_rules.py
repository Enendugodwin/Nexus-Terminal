"""Rule-based diagnosis of connection and command failures.

Rules are ordered and explicit. The engine matches observable error text and
returns likely causes plus *suggested checks* - never automatic remediation.
Remote output is untrusted input and is never treated as instructions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from nexus_remote.util import json_loads


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class ErrorRule:
    id: str
    title: str
    patterns: tuple[str, ...]
    causes: tuple[str, ...]
    next_steps: tuple[str, ...]
    environments: tuple[str, ...] = ("any",)
    protocols: tuple[str, ...] = ("any",)
    confidence: Confidence = Confidence.MEDIUM
    source: str = "Built-in diagnostic rules"

    def applies(self, environment: str, protocol: str | None) -> bool:
        env = (environment or "any").lower()
        proto = (protocol or "any").lower()
        env_ok = "any" in self.environments or env in self.environments
        proto_ok = "any" in self.protocols or proto in self.protocols
        return env_ok and proto_ok


@dataclass(frozen=True)
class ErrorDiagnosis:
    rule_id: str
    title: str
    confidence: Confidence
    causes: tuple[str, ...]
    next_steps: tuple[str, ...]
    source: str
    matched_text: str = ""
    untrusted_input: bool = True
    requires_confirmation: bool = True

    def render(self) -> str:
        lines = ["ERROR DETECTED", f"{self.title}", "", "Possible causes:"]
        lines.extend(f"{index}. {cause}" for index, cause in enumerate(self.causes, start=1))
        lines.extend(["", "Suggested checks:"])
        lines.extend(f"- {step}" for step in self.next_steps)
        lines.extend(
            [
                "",
                f"Source: {self.source}",
                f"Confidence: {self.confidence.value}",
                "Note: remote output was treated as untrusted text. No action is taken "
                "automatically - select a check to run it explicitly.",
            ]
        )
        return "\n".join(lines)


BUILT_IN_RULES: tuple[ErrorRule, ...] = (
    ErrorRule(
        id="tcp-timeout",
        title="Connection timed out",
        patterns=(
            r"(?i)\btimed?\s*out\b",
            r"(?i)connection timeout",
            r"(?i)operation timed out",
            r"(?i)no response from (?:the )?(?:host|server)",
        ),
        causes=(
            "The destination is unreachable from this network.",
            "A firewall or ACL is dropping packets to this port.",
            "A required VPN route or jump host is not active.",
            "The device is powered off or the address is wrong.",
        ),
        next_steps=(
            "Verify the hostname resolves to the expected address.",
            "Inspect the local route to the destination.",
            "Test the destination TCP port from this machine.",
            "Confirm any required VPN or gateway connection is up.",
        ),
        confidence=Confidence.MEDIUM,
    ),
    ErrorRule(
        id="tcp-refused",
        title="Connection refused",
        patterns=(r"(?i)connection refused", r"(?i)actively refused", r"(?i)errno 111"),
        causes=(
            "The host is reachable but nothing is listening on that port.",
            "The service is stopped or bound to a different interface.",
            "A local firewall is rejecting the connection (reset).",
        ),
        next_steps=(
            "Confirm the service is running on the device.",
            "Check the port number in the device profile.",
            "Test another known-open port to confirm the host is reachable.",
        ),
        confidence=Confidence.HIGH,
    ),
    ErrorRule(
        id="dns-failure",
        title="Hostname could not be resolved",
        patterns=(
            r"(?i)name or service not known",
            r"(?i)could not resolve host",
            r"(?i)temporary failure in name resolution",
            r"(?i)getaddrinfo failed",
            r"(?i)no such host",
        ),
        causes=(
            "The hostname is misspelled.",
            "The local DNS servers cannot resolve this name.",
            "The device is only known by IP on this network.",
        ),
        next_steps=(
            "Check the spelling in the device profile.",
            "Try the IP address directly.",
            "Confirm the configured DNS servers are reachable.",
        ),
        confidence=Confidence.HIGH,
    ),
    ErrorRule(
        id="ssh-auth-failed",
        title="SSH authentication failed",
        patterns=(
            r"(?i)permission denied \(publickey",
            r"(?i)authentication failed",
            r"(?i)too many authentication failures",
            r"(?i)no supported authentication methods",
        ),
        causes=(
            "The username or key is not authorised on the server.",
            "The SSH agent offered too many keys before the correct one.",
            "The account is locked or restricted to another source address.",
            "The required key passphrase was not available.",
        ),
        next_steps=(
            "Verify the username and the key stored for this device.",
            "Check the server's authorized_keys for the matching public key.",
            "Reduce agent identities or use IdentitiesOnly for this host.",
            "Review the server's authentication log for the rejection reason.",
        ),
        protocols=("ssh", "sftp"),
        confidence=Confidence.HIGH,
    ),
    ErrorRule(
        id="ssh-host-key",
        title="SSH host key verification failed",
        patterns=(
            r"(?i)host key verification failed",
            r"(?i)remote host identification has changed",
            r"(?i)host key .* does not match",
        ),
        causes=(
            "The server was reinstalled or re-keyed.",
            "The saved fingerprint belongs to a different device at this address.",
            "Traffic is being intercepted (man-in-the-middle).",
        ),
        next_steps=(
            "Compare the presented fingerprint with the device owner's record out-of-band.",
            "Review the stored key in the Security Center before trusting a new one.",
            "Do not accept a changed key until it has been verified.",
        ),
        protocols=("ssh", "sftp"),
        confidence=Confidence.HIGH,
    ),
    ErrorRule(
        id="ssh-handshake",
        title="SSH handshake failed before authentication",
        patterns=(
            r"(?i)kex_exchange_identification",
            r"(?i)ssh_exchange_identification",
            r"(?i)connection reset by peer",
            r"(?i)banner exchange",
        ),
        causes=(
            "The port is served by something other than SSH.",
            "A proxy, firewall or load balancer is interfering with the connection.",
            "The server is rate-limiting or blocking this source address.",
            "The SSH daemon is overloaded or restarting.",
        ),
        next_steps=(
            "Run the protocol probe to read the service banner.",
            "Confirm the port is not a proxy or console server.",
            "Check the server's SSH daemon and firewall logs.",
        ),
        protocols=("ssh", "sftp"),
        confidence=Confidence.MEDIUM,
    ),
    ErrorRule(
        id="network-unreachable",
        title="Network unreachable",
        patterns=(
            r"(?i)network is unreachable",
            r"(?i)no route to host",
            r"(?i)destination host unreachable",
            r"(?i)general failure",
        ),
        causes=(
            "This machine has no route to the destination network.",
            "A VPN interface is down or missing the required route.",
            "The default gateway cannot reach the destination network.",
        ),
        next_steps=(
            "Inspect the local routing table for a matching route.",
            "Connect the required VPN and re-check the route.",
            "Confirm the destination network is reachable from this site.",
        ),
        confidence=Confidence.HIGH,
    ),
    ErrorRule(
        id="rdp-login",
        title="RDP authentication or logon failure",
        patterns=(
            r"(?i)0x204\b",
            r"(?i)0x516\b",
            r"(?i)0x904\b",
            r"(?i)logon attempt failed",
            r"(?i)the credentials that were used to connect.*did not work",
            r"(?i)nla",
        ),
        causes=(
            "The credentials are not valid for this host.",
            "Network Level Authentication is required but not satisfied.",
            "The account is not permitted to log on remotely.",
            "The host certificate or gateway trust is not configured.",
        ),
        next_steps=(
            "Verify the account can log on locally/remotely on the host.",
            "Confirm NLA requirements and the RDP profile settings.",
            "Review the target's Security event log for logon failures.",
        ),
        protocols=("rdp",),
        confidence=Confidence.MEDIUM,
    ),
    ErrorRule(
        id="smb-access",
        title="SMB access denied or share unavailable",
        patterns=(
            r"(?i)access is denied",
            r"(?i)bad network name",
            r"(?i)logon failure",
            r"(?i)the network path was not found",
            r"(?i)STATUS_ACCESS_DENIED",
            r"(?i)STATUS_LOGON_FAILURE",
        ),
        causes=(
            "The account lacks share or NTFS permissions.",
            "The share name is incorrect or the share is not published.",
            "SMB signing or encryption is required and not negotiated.",
            "The server blocks the protocol version offered by the client.",
        ),
        next_steps=(
            "Confirm the share name and the account's share permissions.",
            "Check SMB signing/encryption requirements on the server.",
            "Verify the server is reachable on port 445.",
        ),
        protocols=("smb",),
        confidence=Confidence.MEDIUM,
    ),
    ErrorRule(
        id="command-not-found",
        title="Command not found",
        patterns=(
            r"(?i)command not found",
            r"(?i)is not recognized as an internal or external command",
            r"(?i)The term '.*' is not recognized",
            r"(?i)% ?Invalid input detected",
            r"(?i)unknown command",
        ),
        causes=(
            "The command does not exist in this environment.",
            "The command was suggested for a different shell or vendor CLI.",
            "The tool is not installed or not on the PATH.",
        ),
        next_steps=(
            "Check the environment label for the session.",
            "Ask the assistant for a suggestion valid in this shell.",
            "Locate the tool's install path or package.",
        ),
        confidence=Confidence.HIGH,
    ),
    ErrorRule(
        id="permission-denied",
        title="Permission denied",
        patterns=(r"(?i)^permission denied", r"(?i)access denied", r"(?i)operation not permitted"),
        causes=(
            "The account lacks the required privilege on the remote host.",
            "The file or directory permissions do not allow the operation.",
            "Sudo/administrator elevation is required.",
        ),
        next_steps=(
            "Check ownership and permissions on the target path.",
            "Confirm the account's privileges on the device.",
            "Use an approved elevation path if policy allows it.",
        ),
        confidence=Confidence.MEDIUM,
    ),
    ErrorRule(
        id="disk-full",
        title="No space left on device",
        patterns=(r"(?i)no space left on device", r"(?i)disk quota exceeded", r"(?i)not enough space"),
        causes=(
            "The destination filesystem is full.",
            "A quota limits the account's storage.",
        ),
        next_steps=(
            "Check free space with df -h (Linux) or Get-Volume (Windows).",
            "Remove or move data with the owner's approval.",
            "Choose a different destination for the transfer.",
        ),
        confidence=Confidence.HIGH,
    ),
)


class ErrorDiagnosisEngine:
    """Matches error text against ordered rules and stored known errors."""

    def __init__(self, rules: Sequence[ErrorRule] | None = None) -> None:
        self.rules = list(rules if rules is not None else BUILT_IN_RULES)

    def diagnose(
        self,
        output: str,
        environment: str = "unknown",
        protocol: str | None = None,
    ) -> ErrorDiagnosis | None:
        text = output or ""
        if not text.strip():
            return None
        for rule in self.rules:
            if not rule.applies(environment, protocol):
                continue
            for pattern in rule.patterns:
                match = re.search(pattern, text, re.MULTILINE)
                if match:
                    return ErrorDiagnosis(
                        rule_id=rule.id,
                        title=rule.title,
                        confidence=rule.confidence,
                        causes=rule.causes,
                        next_steps=rule.next_steps,
                        source=rule.source,
                        matched_text=match.group(0)[:200],
                    )
        return None

    def diagnose_all(
        self, output: str, environment: str = "unknown", protocol: str | None = None
    ) -> list[ErrorDiagnosis]:
        text = output or ""
        found: list[ErrorDiagnosis] = []
        for rule in self.rules:
            if not rule.applies(environment, protocol):
                continue
            for pattern in rule.patterns:
                match = re.search(pattern, text, re.MULTILINE)
                if match:
                    found.append(
                        ErrorDiagnosis(
                            rule_id=rule.id,
                            title=rule.title,
                            confidence=rule.confidence,
                            causes=rule.causes,
                            next_steps=rule.next_steps,
                            source=rule.source,
                            matched_text=match.group(0)[:200],
                        )
                    )
                    break
        return found

    # -- stored knowledge ---------------------------------------------------
    def load_stored_rules(self, database: object) -> list[ErrorRule]:
        """Load user-confirmed error knowledge from the ``known_errors`` table."""
        rows = database.query("SELECT * FROM known_errors")  # type: ignore[attr-defined]
        return [
            ErrorRule(
                id=row["id"],
                title=row["title"],
                patterns=(row["pattern"],),
                causes=tuple(json_loads(row["causes"], [])),
                next_steps=tuple(json_loads(row["next_steps"], [])),
                environments=(row["environment"],) if row["environment"] != "any" else ("any",),
                source=row["source"] or "Learned from your sessions",
            )
            for row in rows
        ]

    def rule_ids(self) -> Iterable[str]:
        return (rule.id for rule in self.rules)
