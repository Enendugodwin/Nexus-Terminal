"""Secret redaction.

Terminal output, banners, command output and log records are all untrusted
text. Anything that looks like a secret is masked before it is written to a log
file, the audit trail or the session transcript.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

MASK = "***REDACTED***"

#: Field names whose values are secret regardless of the surrounding text.
SENSITIVE_KEY_NAMES = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_key",
        "client_secret",
        "passphrase",
        "private_key",
        "privatekey",
        "credential",
        "credentials",
        "enrollment_secret",
    }
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # key=value / key: value secrets
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
            r"client[_-]?secret|passphrase|private[_-]?key)\b\s*[:=]\s*(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        rf"\1={MASK}",
    ),
    # CLI flags: --password secret / -p secret
    (
        re.compile(r"(?i)(--password|--passwd|--token|--api-key|-pw|-p)\s+(\"[^\"]*\"|'[^']*'|\S+)"),
        rf"\1 {MASK}",
    ),
    # HTTP auth headers
    (re.compile(r"(?i)(authorization\s*:\s*)(bearer|basic|token)\s+\S+"), rf"\1\2 {MASK}"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"), rf"\1 {MASK}"),
    # Private key material
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        MASK,
    ),
    # Cloud / API key shapes
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), MASK),
    # OpenSSH private key body marker
    (re.compile(r"(?i)\bOPENSSH PRIVATE KEY\b"), MASK),
    # Connection strings with credentials
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^:/@\s]+):([^@/\s]+)@"), rf"\1\2:{MASK}@" ),
)


def redact(text: str) -> str:
    """Mask anything in ``text`` that looks like a secret."""
    if not text:
        return text
    result = text
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_output(text: str, extra_secrets: Iterable[str] | None = None) -> str:
    """Mask pattern-based secrets and any literal secrets passed in."""
    result = redact(text)
    for secret in extra_secrets or ():
        if secret and len(str(secret)) >= 4:
            result = result.replace(str(secret), MASK)
    return result


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from messages and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True
