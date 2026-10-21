"""Service container: builds and wires every non-UI subsystem.

Both the desktop application and the tests construct the object graph here, so
there is exactly one place where storage, security, diagnostics, the assistant
and the connection layer are joined together.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

from nexus_remote import config
from nexus_remote.assistant.autocomplete import CompletionEngine
from nexus_remote.assistant.catalog import load_environment_map
from nexus_remote.assistant.command_history import CommandHistory
from nexus_remote.assistant.error_rules import ErrorDiagnosisEngine
from nexus_remote.assistant.model_adapters import ModelRegistry, NullModel
from nexus_remote.assistant.playbooks import PlaybookLibrary
from nexus_remote.assistant.providers import AIConfig, build_model, load_ai_config, preset_for
from nexus_remote.assistant.snippets import SnippetStore
from nexus_remote.connections.manager import SessionManager
from nexus_remote.connections.routing import RoutingEngine
from nexus_remote.devices.repository import DeviceRepository
from nexus_remote.diagnostics.reports import DiagnosticsRunner
from nexus_remote.security.audit import AuditLog
from nexus_remote.security.credentials import CredentialService, CredentialStore, SecretBackend
from nexus_remote.security.host_keys import KnownHosts
from nexus_remote.security.policies import PolicyEngine
from nexus_remote.security.redact import RedactingFilter
from nexus_remote.storage.database import Database

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


@dataclass
class AppServices:
    """The application's service graph."""

    paths: config.Paths
    database: Database
    devices: DeviceRepository
    credentials: CredentialService
    audit: AuditLog
    host_keys: KnownHosts
    history: CommandHistory
    autocomplete: CompletionEngine
    errors: ErrorDiagnosisEngine
    playbooks: PlaybookLibrary
    snippets: SnippetStore
    diagnostics: DiagnosticsRunner
    policy: PolicyEngine
    routing: RoutingEngine
    sessions: SessionManager
    models: ModelRegistry

    def close(self) -> None:
        self.sessions.close_all()
        self.database.close()

    # -- convenience --------------------------------------------------------
    @property
    def relay_allowlist(self) -> list[str]:
        raw = self.database.get_setting("relay.allowed_destinations", "") or ""
        return [item.strip() for item in raw.split(",") if item.strip()]

    def save_relay_allowlist(self, destinations: list[str]) -> None:
        self.database.set_setting(
            "relay.allowed_destinations", ",".join(item.strip() for item in destinations if item.strip())
        )
        self.policy.relay_allowed_destinations = self.relay_allowlist

    # -- vendor catalog -----------------------------------------------------
    @property
    def catalog_feed_url(self) -> str:
        return (self.database.get_setting("catalog.feed_url", "") or "").strip()

    def save_catalog_feed_url(self, url: str) -> None:
        self.database.set_setting("catalog.feed_url", (url or "").strip())

    @property
    def catalog_auto_check(self) -> bool:
        return (self.database.get_setting("catalog.auto_check", "1") or "1") == "1"

    def save_catalog_auto_check(self, enabled: bool) -> None:
        self.database.set_setting("catalog.auto_check", "1" if enabled else "0")

    def reload_catalog(self) -> None:
        """Rebuild the completion engine, picking up a downloaded catalog."""
        self.autocomplete = CompletionEngine(
            downloaded=load_environment_map(self.paths.catalog)
        )

    # -- optional AI --------------------------------------------------------
    @property
    def ai_config(self) -> AIConfig:
        return load_ai_config(self.database)

    @property
    def ai_provider_label(self) -> str:
        return preset_for(self.ai_config.provider).label

    @staticmethod
    def _ai_key_name(provider: str) -> str:
        return f"ai:api_token:{(provider or 'disabled').strip() or 'disabled'}"

    def ai_api_key(self, provider: str | None = None) -> str:
        """Read a provider key from the OS vault (never from SQLite)."""

        key = self._ai_key_name(provider or self.ai_config.provider)
        return self.credentials.store.get(key) or ""

    def ai_key_configured(self, provider: str | None = None) -> bool:
        return bool(self.ai_api_key(provider))

    def save_ai_api_key(self, provider: str, secret: str) -> None:
        value = (secret or "").strip()
        if not value:
            raise ValueError("refusing to store an empty API key")
        self.credentials.store.backend.set(self._ai_key_name(provider), value)

    def clear_ai_api_key(self, provider: str) -> None:
        self.credentials.store.backend.delete(self._ai_key_name(provider))

    def configure_ai_from_settings(self) -> None:
        """Rebuild the active model from saved settings and the vault.

        Construction is offline; hosted providers do not contact their API until
        the operator explicitly asks a question or presses Test.
        """

        config = self.ai_config
        try:
            # A stored key is always used, even for providers where it is
            # optional: an OpenAI-compatible endpoint (OpenRouter, an API
            # gateway, ...) rejects requests with "Missing API key" if the
            # key is dropped simply because a local server would not need one.
            key = self.ai_api_key(config.provider)
            model = build_model(config, api_key=key)
        except Exception:
            model = NullModel()
        self.models.configure(model)

    # -- application updates -------------------------------------------------
    @property
    def update_feed_url(self) -> str:
        return (self.database.get_setting("update.feed_url", "") or "").strip()

    def save_update_feed_url(self, url: str) -> None:
        self.database.set_setting("update.feed_url", (url or "").strip())

    @property
    def update_auto_check(self) -> bool:
        return (self.database.get_setting("update.auto_check", "1") or "1") == "1"

    def save_update_auto_check(self, enabled: bool) -> None:
        self.database.set_setting("update.auto_check", "1" if enabled else "0")


def build_services(
    paths: config.Paths | None = None,
    *,
    credential_backend: SecretBackend | None = None,
    policy: PolicyEngine | None = None,
) -> AppServices:
    resolved = (paths or config.default_paths()).ensure()
    database = Database(resolved.database).initialize()
    repository = DeviceRepository(database)
    credentials = CredentialService(database, CredentialStore(credential_backend))
    audit = AuditLog(database, resolved.logs / "audit.jsonl")
    host_keys = KnownHosts(database, resolved.known_hosts)
    history = CommandHistory(database)

    relay_setting = database.get_setting("relay.allowed_destinations", "") or ""
    active_policy = policy or PolicyEngine(
        allow_destructive_commands=(database.get_setting("policy.allow_destructive", "0") or "0") == "1",
        require_confirmation_for_state_changes=(
            database.get_setting("policy.confirm_state_changes", "1") or "1"
        )
        == "1",
        relay_allowed_destinations=[item.strip() for item in relay_setting.split(",") if item.strip()],
        allow_intrusive_tools=(database.get_setting("policy.allow_intrusive", "0") or "0") == "1",
    )

    sessions = SessionManager(
        repository,
        credentials=credentials,
        audit=audit,
        policy=active_policy,
        routing=RoutingEngine(active_policy),
        history=history,
    )
    from nexus_remote.protocols import register_adapters

    register_adapters(sessions, host_keys)
    services = AppServices(
        paths=resolved,
        database=database,
        devices=repository,
        credentials=credentials,
        audit=audit,
        host_keys=host_keys,
        history=history,
        autocomplete=CompletionEngine(
            downloaded=load_environment_map(resolved.catalog)
        ),
        errors=ErrorDiagnosisEngine(),
        playbooks=PlaybookLibrary(),
        snippets=SnippetStore(database),
        diagnostics=DiagnosticsRunner(),
        policy=active_policy,
        routing=sessions.routing,
        sessions=sessions,
        models=ModelRegistry(),
    )
    # Restore the operator's chosen provider on every launch, not only after
    # the Settings page is saved. This performs no network access.
    services.configure_ai_from_settings()
    return services


def configure_logging(paths: config.Paths | None = None, *, level: int = logging.INFO) -> logging.Logger:
    """Configure application logging with mandatory secret redaction."""
    resolved = (paths or config.default_paths()).ensure()
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        file_handler = RotatingFileHandler(
            resolved.logs / "nexus-remote.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        file_handler.addFilter(RedactingFilter())
        root.addHandler(file_handler)
    if not any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, RotatingFileHandler)
        for handler in root.handlers
    ) and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter(LOG_FORMAT))
        stream.addFilter(RedactingFilter())
        root.addHandler(stream)
    return logging.getLogger(config.APP_SLUG)
