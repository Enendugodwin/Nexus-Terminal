"""SSH host-key verification.

Unknown keys are never trusted automatically; a changed key is treated as a
possible man-in-the-middle until the user explicitly accepts the new
fingerprint.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from nexus_remote.storage.database import Database
from nexus_remote.util import utcnow_iso


class HostKeyDecision(str, Enum):
    TRUSTED = "trusted"
    UNKNOWN = "unknown"
    CHANGED = "changed"
    REVOKED = "revoked"


@dataclass(frozen=True)
class HostKeyVerdict:
    decision: HostKeyDecision
    host: str
    key_type: str
    fingerprint: str
    known_fingerprint: str | None = None
    message: str = ""

    @property
    def trusted(self) -> bool:
        return self.decision is HostKeyDecision.TRUSTED


def fingerprint_of(key: Any) -> str:
    """OpenSSH-style SHA256 fingerprint for a paramiko key or raw blob."""
    if hasattr(key, "asbytes"):
        blob = key.asbytes()
    elif isinstance(key, (bytes, bytearray)):
        blob = bytes(key)
    else:  # pragma: no cover - defensive
        raise TypeError("cannot fingerprint this object")
    digest = hashlib.sha256(blob).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


class UntrustedHostKeyError(Exception):
    """Raised by the paramiko policy when a key needs a user decision."""

    def __init__(self, verdict: HostKeyVerdict, key: Any = None) -> None:
        super().__init__(verdict.message or "host key is not trusted")
        self.verdict = verdict
        self.key = key


class KnownHosts:
    """Key store backed by SQLite, optionally mirrored to an OpenSSH file."""

    def __init__(self, database: Database, file_path: str | Path | None = None) -> None:
        self.db = database
        self.file_path = Path(file_path) if file_path else None

    # -- lookups ------------------------------------------------------------
    def lookup(self, host: str, key_type: str) -> str | None:
        row = self.db.query_one(
            "SELECT fingerprint, status FROM host_keys WHERE host = ? AND key_type = ?",
            (host, key_type),
        )
        if row is None or row["status"] != "trusted":
            return None
        return row["fingerprint"]

    def verify(self, host: str, key_type: str, key: Any) -> HostKeyVerdict:
        fingerprint = fingerprint_of(key)
        row = self.db.query_one(
            "SELECT fingerprint, status FROM host_keys WHERE host = ? AND key_type = ?",
            (host, key_type),
        )
        if row is None:
            return HostKeyVerdict(
                decision=HostKeyDecision.UNKNOWN,
                host=host,
                key_type=key_type,
                fingerprint=fingerprint,
                message=(
                    f"first connection to {host}: {key_type} key {fingerprint} has not been "
                    "verified. Accept it only if it matches the device you expect."
                ),
            )
        known = row["fingerprint"]
        if row["status"] != "trusted":
            return HostKeyVerdict(
                decision=HostKeyDecision.REVOKED,
                host=host,
                key_type=key_type,
                fingerprint=fingerprint,
                known_fingerprint=known,
                message=f"the stored key for {host} was revoked; re-verify before trusting it again.",
            )
        if known != fingerprint:
            return HostKeyVerdict(
                decision=HostKeyDecision.CHANGED,
                host=host,
                key_type=key_type,
                fingerprint=fingerprint,
                known_fingerprint=known,
                message=(
                    f"HOST KEY CHANGED for {host}: stored {known}, presented {fingerprint}. "
                    "This can indicate a rebuilt server or an interception attempt."
                ),
            )
        return HostKeyVerdict(
            decision=HostKeyDecision.TRUSTED,
            host=host,
            key_type=key_type,
            fingerprint=fingerprint,
            known_fingerprint=known,
            message=f"{host} host key verified ({fingerprint}).",
        )

    # -- updates ------------------------------------------------------------
    def trust(self, host: str, key_type: str, key: Any, *, source: str = "user") -> HostKeyVerdict:
        fingerprint = fingerprint_of(key)
        blob = getattr(key, "get_base64", lambda: None)()
        self.db.execute(
            "INSERT INTO host_keys (host, key_type, fingerprint, key_blob, status, source, trusted_at) "
            "VALUES (?, ?, ?, ?, 'trusted', ?, ?) "
            "ON CONFLICT(host, key_type) DO UPDATE SET fingerprint = excluded.fingerprint, "
            "key_blob = excluded.key_blob, status = 'trusted', source = excluded.source, "
            "trusted_at = excluded.trusted_at",
            (host, key_type, fingerprint, blob, source, utcnow_iso()),
        )
        if self.file_path is not None:
            self._append_known_hosts_line(host, key_type, blob)
        return HostKeyVerdict(
            decision=HostKeyDecision.TRUSTED,
            host=host,
            key_type=key_type,
            fingerprint=fingerprint,
            known_fingerprint=fingerprint,
            message=f"{host} {key_type} key trusted ({fingerprint}).",
        )

    def revoke(self, host: str, key_type: str | None = None) -> int:
        if key_type:
            cursor = self.db.execute(
                "UPDATE host_keys SET status = 'revoked' WHERE host = ? AND key_type = ?",
                (host, key_type),
            )
        else:
            cursor = self.db.execute("UPDATE host_keys SET status = 'revoked' WHERE host = ?", (host,))
        return cursor.rowcount

    def forget(self, host: str, key_type: str | None = None) -> int:
        if key_type:
            cursor = self.db.execute(
                "DELETE FROM host_keys WHERE host = ? AND key_type = ?", (host, key_type)
            )
        else:
            cursor = self.db.execute("DELETE FROM host_keys WHERE host = ?", (host,))
        return cursor.rowcount

    def entries(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.db.query("SELECT * FROM host_keys WHERE status = ? ORDER BY host, key_type", (status,))
        else:
            rows = self.db.query("SELECT * FROM host_keys ORDER BY host, key_type")
        return [dict(row) for row in rows]

    def _append_known_hosts_line(self, host: str, key_type: str, blob: str | None) -> None:
        if self.file_path is None or not blob:
            return
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        line = f"{host} {key_type} {blob}\n"
        existing = ""
        if self.file_path.exists():
            existing = self.file_path.read_text(encoding="utf-8")
        if line in existing:
            return
        with open(self.file_path, "a", encoding="utf-8") as handle:
            handle.write(line)

    # -- paramiko integration ----------------------------------------------
    def paramiko_policy(self) -> Any:
        """Return a paramiko MissingHostKeyPolicy that refuses unverified keys.

        Paramiko raises whatever the policy raises, so the UI gets an
        :class:`UntrustedHostKeyError` carrying a verdict it can display.
        """
        known_hosts = self

        try:
            import paramiko
        except ImportError:  # pragma: no cover - paramiko is a hard dependency
            raise RuntimeError("paramiko is required for host-key verification") from None

        class _Policy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
                verdict = known_hosts.verify(hostname, key.get_name(), key)
                if not verdict.trusted:
                    raise UntrustedHostKeyError(verdict, key)

        return _Policy()

    # -- pending decisions --------------------------------------------------
    def record_pending(self, verdict: HostKeyVerdict, key: Any = None) -> None:
        """Store a presented key as 'pending' so it can be reviewed and trusted.

        The blob is kept so accepting the key later is a status change rather
        than a second connection to an unverified host.
        """
        if verdict.decision not in (HostKeyDecision.UNKNOWN, HostKeyDecision.CHANGED):
            return
        blob = None
        if key is not None and hasattr(key, "get_base64"):
            blob = key.get_base64()
        self.db.execute(
            "INSERT INTO host_keys (host, key_type, fingerprint, key_blob, status, source, trusted_at) "
            "VALUES (?, ?, ?, ?, 'pending', 'presented-by-server', ?) "
            "ON CONFLICT(host, key_type) DO UPDATE SET fingerprint = excluded.fingerprint, "
            "key_blob = excluded.key_blob, status = 'pending', source = excluded.source, "
            "trusted_at = excluded.trusted_at",
            (verdict.host, verdict.key_type, verdict.fingerprint, blob, utcnow_iso()),
        )

    def pending(self) -> list[dict[str, Any]]:
        return self.entries(status="pending")

    def trust_pending(self, host: str, key_type: str) -> bool:
        """Accept a previously presented key (explicit operator decision)."""
        row = self.db.query_one(
            "SELECT fingerprint, key_blob FROM host_keys WHERE host = ? AND key_type = ? AND status = 'pending'",
            (host, key_type),
        )
        if row is None:
            return False
        self.db.execute(
            "UPDATE host_keys SET status = 'trusted', source = 'user', trusted_at = ? "
            "WHERE host = ? AND key_type = ?",
            (utcnow_iso(), host, key_type),
        )
        self._append_known_hosts_line(host, key_type, row["key_blob"])
        return True


def load_known_hosts_file(path: str | Path) -> Iterable[tuple[str, str, str]]:
    """Parse an OpenSSH ``known_hosts`` file into (host, key_type, base64)."""
    source = Path(path)
    if not source.exists():
        return []
    entries: list[tuple[str, str, str]] = []
    for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "|")):
            continue
        parts = line.split()
        if len(parts) >= 3:
            entries.append((parts[0], parts[1], parts[2]))
    return entries
