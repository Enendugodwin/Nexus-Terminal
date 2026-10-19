"""Security subsystem: credential vault, host keys, policies, redaction, audit."""

from nexus_remote.security.audit import AuditEvent, AuditLog
from nexus_remote.security.credentials import CredentialError, CredentialService, CredentialStore
from nexus_remote.security.host_keys import HostKeyDecision, HostKeyVerdict, KnownHosts
from nexus_remote.security.policies import PolicyEngine, RiskLevel, classify_command
from nexus_remote.security.redact import RedactingFilter, redact, redact_output

__all__ = [
    "AuditEvent",
    "AuditLog",
    "CredentialError",
    "CredentialService",
    "CredentialStore",
    "HostKeyDecision",
    "HostKeyVerdict",
    "KnownHosts",
    "PolicyEngine",
    "RedactingFilter",
    "RiskLevel",
    "classify_command",
    "redact",
    "redact_output",
]
