"""Credential storage backed by the operating-system secret store.

On Windows the backend is the Windows Credential Manager (via ``keyring``). The
database only ever stores a *reference* (the vault key name); passwords, private
keys and tokens never touch SQLite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from nexus_remote.storage.database import Database
from nexus_remote.util import utcnow_iso

SERVICE_PREFIX = "nexus-remote"

#: Categories of secret the vault can hold.
CREDENTIAL_KINDS = (
    "password",
    "ssh_private_key",
    "ssh_key_passphrase",
    "api_token",
    "cloud_credential",
    "gateway_credential",
    "agent_enrollment",
)


class CredentialError(RuntimeError):
    """Raised when the vault is unavailable or refused an operation."""


class SecretBackend(Protocol):
    """The subset of the ``keyring`` API this application relies on."""

    name: str
    persistent: bool

    def set(self, key: str, secret: str) -> None: ...
    def get(self, key: str) -> str | None: ...
    def delete(self, key: str) -> None: ...
    def list_keys(self) -> list[str]: ...


class MemoryBackend:
    """Non-persistent backend used by tests and as a last-resort fallback."""

    name = "in-memory (not persistent)"
    persistent = False

    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def set(self, key: str, secret: str) -> None:
        self._items[key] = secret

    def get(self, key: str) -> str | None:
        return self._items.get(key)

    def delete(self, key: str) -> None:
        self._items.pop(key, None)

    def list_keys(self) -> list[str]:
        return sorted(self._items)


class KeyringBackend:
    """Adapter over the ``keyring`` package (Windows Credential Manager)."""

    persistent = True

    def __init__(self) -> None:
        import keyring  # imported lazily so the core works without it
        from keyring.backends.fail import Keyring as FailKeyring

        backend = keyring.get_keyring()
        if isinstance(backend, FailKeyring):
            raise CredentialError("no usable OS credential backend is available")
        self._keyring = keyring
        self.name = f"{backend.__class__.__module__}.{backend.__class__.__name__}"

    def set(self, key: str, secret: str) -> None:
        try:
            self._keyring.set_password(SERVICE_PREFIX, key, secret)
        except Exception as exc:  # pragma: no cover - backend specific
            raise CredentialError(f"could not store secret: {exc}") from exc

    def get(self, key: str) -> str | None:
        try:
            return self._keyring.get_password(SERVICE_PREFIX, key)
        except Exception as exc:  # pragma: no cover - backend specific
            raise CredentialError(f"could not read secret: {exc}") from exc

    def delete(self, key: str) -> None:
        try:
            self._keyring.delete_password(SERVICE_PREFIX, key)
        except Exception:  # missing entries are not an error for callers
            return

    def list_keys(self) -> list[str]:
        # The keyring API has no portable enumeration; the database is the
        # index of what we stored.
        return []


class CredentialStore:
    """High-level vault: opaque key names in, secrets out."""

    def __init__(self, backend: SecretBackend | None = None) -> None:
        if backend is None:
            try:
                backend = KeyringBackend()
            except Exception:
                backend = MemoryBackend()
        self.backend = backend

    @property
    def persistent(self) -> bool:
        return bool(getattr(self.backend, "persistent", False))

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", self.backend.__class__.__name__)

    @staticmethod
    def key_name(device_id: str, kind: str, username: str = "") -> str:
        user = (username or "-").strip() or "-"
        return f"{device_id}:{kind}:{user}"

    def store(self, device_id: str, kind: str, username: str, secret: str) -> str:
        if not secret:
            raise CredentialError("refusing to store an empty secret")
        key = self.key_name(device_id, kind, username)
        self.backend.set(key, secret)
        return key

    def get(self, key: str) -> str | None:
        return self.backend.get(key)

    def get_for(self, device_id: str, kind: str, username: str = "") -> str | None:
        return self.backend.get(self.key_name(device_id, kind, username))

    def delete(self, key: str) -> None:
        self.backend.delete(key)

    def has(self, key: str) -> bool:
        return self.backend.get(key) is not None


@dataclass(frozen=True)
class CredentialReference:
    """A row of ``credential_refs`` - a pointer, never a secret."""

    id: str
    device_id: str | None
    kind: str
    username: str
    key_name: str
    created_at: str


class CredentialService:
    """Ties the vault to the credential-reference table in the database."""

    def __init__(self, database: Database, store: CredentialStore | None = None) -> None:
        self.db = database
        self.store = store or CredentialStore()

    # -- write ---------------------------------------------------------------
    def save(
        self,
        device_id: str,
        kind: str,
        username: str,
        secret: str,
        *,
        label: str = "",
    ) -> CredentialReference:
        if kind not in CREDENTIAL_KINDS:
            raise CredentialError(f"unknown credential kind {kind!r}")
        key = self.store.store(device_id, kind, username, secret)
        existing = self.db.query_one("SELECT id, created_at FROM credential_refs WHERE key_name = ?", (key,))
        if existing:
            reference = CredentialReference(
                id=existing["id"],
                device_id=device_id,
                kind=kind,
                username=username,
                key_name=key,
                created_at=existing["created_at"],
            )
        else:
            reference = CredentialReference(
                id=uuid.uuid4().hex,
                device_id=device_id,
                kind=kind,
                username=username,
                key_name=key,
                created_at=utcnow_iso(),
            )
            self.db.execute(
                "INSERT INTO credential_refs (id, device_id, kind, username, key_name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    reference.id,
                    reference.device_id,
                    reference.kind,
                    reference.username,
                    reference.key_name,
                    reference.created_at,
                ),
            )
        return reference

    # -- read ----------------------------------------------------------------
    def resolve(self, device_id: str, kind: str, username: str = "") -> str | None:
        return self.store.get_for(device_id, kind, username)

    def references(self, device_id: str | None = None) -> list[CredentialReference]:
        if device_id:
            rows = self.db.query(
                "SELECT * FROM credential_refs WHERE device_id = ? ORDER BY kind, username", (device_id,)
            )
        else:
            rows = self.db.query("SELECT * FROM credential_refs ORDER BY device_id, kind, username")
        return [
            CredentialReference(
                id=row["id"],
                device_id=row["device_id"],
                kind=row["kind"],
                username=row["username"],
                key_name=row["key_name"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # -- delete --------------------------------------------------------------
    def revoke(self, reference: CredentialReference) -> None:
        self.store.delete(reference.key_name)
        self.db.execute("DELETE FROM credential_refs WHERE id = ?", (reference.id,))

    def revoke_device(self, device_id: str) -> int:
        references = self.references(device_id)
        for reference in references:
            self.revoke(reference)
        return len(references)

    def describe(self, reference: CredentialReference) -> dict[str, Any]:
        """Metadata safe to display or log (never the secret itself)."""
        return {
            "device_id": reference.device_id,
            "kind": reference.kind,
            "username": reference.username,
            "key_name": reference.key_name,
            "stored": self.store.has(reference.key_name),
            "created_at": reference.created_at,
        }
