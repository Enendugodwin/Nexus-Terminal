"""Structured troubleshooting playbooks.

Playbooks are deterministic checklists. They describe observations and the
next check to run; they never assert a root cause the tests cannot prove.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaybookStep:
    title: str
    detail: str
    check: str | None = None
    run_diagnostic: str | None = None


@dataclass(frozen=True)
class Playbook:
    key: str
    title: str
    symptoms: tuple[str, ...]
    steps: tuple[PlaybookStep, ...]

    def render(self) -> str:
        lines = [f"PLAYBOOK: {self.title}", ""]
        for index, step in enumerate(self.steps, start=1):
            lines.append(f"{index}. {step.title}")
            lines.append(f"   {step.detail}")
            if step.check:
                lines.append(f"   Check: {step.check}")
        return "\n".join(lines)


PLAYBOOKS: tuple[Playbook, ...] = (
    Playbook(
        key="cannot-reach-host",
        title="A device cannot be reached",
        symptoms=("timeout", "timed out", "unreachable", "cannot connect", "connection failed"),
        steps=(
            PlaybookStep(
                "Confirm the target is correct",
                "Check the hostname/IP and port in the device profile before testing anything else.",
                check="device profile: hostname, protocol, port",
            ),
            PlaybookStep(
                "Resolve the name",
                "Resolution failure stops the investigation early; it says nothing about the device itself.",
                check="DNS resolution",
                run_diagnostic="dns",
            ),
            PlaybookStep(
                "Check the local route",
                "Look for a matching route and whether a VPN adapter carries it.",
                check="local routing table",
                run_diagnostic="route",
            ),
            PlaybookStep(
                "Test the service port",
                "A TCP test distinguishes 'nothing listening' from 'no response at all'.",
                check="TCP handshake to the protocol port",
                run_diagnostic="tcp",
            ),
            PlaybookStep(
                "Exercise the configured alternatives",
                "If a jump host, gateway or agent is configured, verify it is available rather than "
                "assuming the device is down.",
                check="jump host / gateway / agent heartbeat",
            ),
            PlaybookStep(
                "Do not treat ICMP alone as proof",
                "Many hosts drop ping while SSH or RDP remain reachable.",
                check="ICMP result is advisory only",
            ),
        ),
    ),
    Playbook(
        key="ssh-auth-failure",
        title="SSH rejects authentication",
        symptoms=("permission denied (publickey", "authentication failed", "too many authentication failures"),
        steps=(
            PlaybookStep(
                "Confirm the username",
                "Profiles often default to a generic account that the server does not permit.",
                check="device profile username",
            ),
            PlaybookStep(
                "Verify the key or password reference",
                "The Security Center shows which credential is stored for the device; the value is in "
                "the OS vault, never in the database.",
                check="credential reference present and current",
            ),
            PlaybookStep(
                "Check the server's view",
                "Ask the device owner for the authentication log entry (e.g. /var/log/auth.log).",
                check="server-side rejection reason",
            ),
            PlaybookStep(
                "Reduce agent identities",
                "Too many agent keys can exhaust the server's attempt limit before the right key is tried.",
                check="IdentitiesOnly / explicit key selection",
            ),
        ),
    ),
    Playbook(
        key="host-key-changed",
        title="SSH host key changed",
        symptoms=("host key verification failed", "remote host identification has changed"),
        steps=(
            PlaybookStep(
                "Stop and treat this as untrusted",
                "A changed key can mean a rebuilt server or interception. Never accept it blindly.",
                check="stored vs presented fingerprint",
            ),
            PlaybookStep(
                "Verify out-of-band",
                "Compare the fingerprint with the device owner's record or the console.",
                check="fingerprint confirmed by a second channel",
            ),
            PlaybookStep(
                "Record the decision",
                "Trusting or revoking the key is audited in the Security Center.",
                check="audit event recorded",
            ),
        ),
    ),
    Playbook(
        key="rdp-failure",
        title="Remote desktop will not start",
        symptoms=("rdp", "0x204", "0x516", "nla"),
        steps=(
            PlaybookStep(
                "Test port 3389",
                "An RDP client error can hide a plain network failure.",
                check="TCP 3389",
                run_diagnostic="tcp",
            ),
            PlaybookStep(
                "Confirm NLA and account rights",
                "The account must be allowed to log on through Remote Desktop.",
                check="host logon rights and NLA settings",
            ),
            PlaybookStep(
                "Check gateway requirements",
                "If a gateway is required, the RDP profile must reference it explicitly.",
                check="RDP gateway configured",
            ),
        ),
    ),
    Playbook(
        key="smb-failure",
        title="SMB share is unavailable",
        symptoms=("smb", "access is denied", "bad network name", "logon failure"),
        steps=(
            PlaybookStep(
                "Test port 445",
                "Confirm SMB is reachable before diagnosing permissions.",
                check="TCP 445",
                run_diagnostic="tcp",
            ),
            PlaybookStep(
                "Confirm share and permissions",
                "Share permissions and NTFS permissions are enforced separately.",
                check="share name and ACLs",
            ),
            PlaybookStep(
                "Check signing and encryption",
                "A server requiring signing or encryption rejects clients that do not negotiate it.",
                check="SMB signing/encryption requirements",
            ),
        ),
    ),
    Playbook(
        key="transfer-failed",
        title="A file transfer failed",
        symptoms=("transfer failed", "no space", "permission denied", "connection reset"),
        steps=(
            PlaybookStep(
                "Read the specific error",
                "Permission, space, path and connection errors need different fixes.",
                check="transfer error text",
            ),
            PlaybookStep(
                "Confirm destination space and permissions",
                "Check free space and write permission on the target directory.",
                check="destination filesystem",
            ),
            PlaybookStep(
                "Retry with resume",
                "Interrupted transfers can often resume instead of restarting.",
                check="resume supported by server",
            ),
            PlaybookStep(
                "Verify the connection stayed up",
                "Session drops during long transfers point at keepalive or idle timeouts.",
                check="session keepalive settings",
            ),
        ),
    ),
)


class PlaybookLibrary:
    def __init__(self, playbooks: tuple[Playbook, ...] | None = None) -> None:
        self.playbooks = playbooks if playbooks is not None else PLAYBOOKS

    def all(self) -> list[Playbook]:
        return list(self.playbooks)

    def get(self, key: str) -> Playbook | None:
        for playbook in self.playbooks:
            if playbook.key == key:
                return playbook
        return None

    def match(self, text: str) -> list[Playbook]:
        lowered = (text or "").casefold()
        if not lowered:
            return []
        matches = []
        for playbook in self.playbooks:
            if any(symptom in lowered for symptom in playbook.symptoms):
                matches.append(playbook)
        return matches

    def render(self, key: str) -> str:
        playbook = self.get(key)
        if playbook is None:
            return f"no playbook named {key!r}"
        return playbook.render()
