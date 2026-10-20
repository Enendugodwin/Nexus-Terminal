"""Provider catalogue and settings helpers for optional AI.

The application never enables a hosted model implicitly. A provider is used
only after the operator selects it in **Settings > Optional AI** and (for hosted
services) stores an API key in the operating-system vault. Local endpoints may
speak plain HTTP; anything carrying a secret must use HTTPS unless it is on the
loopback interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse
import uuid

from nexus_remote.assistant.model_adapters import (
    AnthropicModel,
    AssistantModel,
    GeminiModel,
    NullModel,
    OllamaModel,
    OpenAICompatibleModel,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from nexus_remote.storage.database import Database


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@dataclass(frozen=True)
class ProviderPreset:
    """Static defaults for one selectable provider."""

    key: str
    label: str
    endpoint: str
    model: str
    requires_key: bool
    note: str = ""
    api_style: str = "openai"


PROVIDERS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        key="disabled",
        label="Disabled (offline features only)",
        endpoint="",
        model="",
        requires_key=False,
        note="No model is contacted. Suggestions, history, rules and playbooks remain available.",
        api_style="none",
    ),
    ProviderPreset(
        key="ollama",
        label="Ollama (local)",
        endpoint="http://127.0.0.1:11434/api/generate",
        model="llama3.1",
        requires_key=False,
        note="Runs on this computer or a local inference server; no API key is needed.",
        api_style="ollama",
    ),
    ProviderPreset(
        key="openai",
        label="OpenAI (GPT / Codex models)",
        endpoint="https://api.openai.com/v1/chat/completions",
        model="gpt-4o-mini",
        requires_key=True,
        note="Uses the OpenAI Chat Completions API. The key is stored in the OS vault.",
    ),
    ProviderPreset(
        key="anthropic",
        label="Anthropic (Claude)",
        endpoint="https://api.anthropic.com/v1/messages",
        model="claude-sonnet-4-5",
        requires_key=True,
        note="Uses the Anthropic Messages API. The key is stored in the OS vault.",
        api_style="anthropic",
    ),
    ProviderPreset(
        key="gemini",
        label="Google Gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        model="gemini-2.5-flash",
        requires_key=True,
        note="Uses the Gemini generateContent API. The key is stored in the OS vault.",
        api_style="gemini",
    ),
    ProviderPreset(
        key="opencode_go",
        label="OpenCode Go (subscription)",
        endpoint="https://opencode.ai/inference/go/openai/v1/chat/completions",
        model="deepseek-v4.1-flash",
        requires_key=True,
        note=(
            "Uses the OpenCode Go / Go Plus subscription allowance. The pay-as-you-go "
            "Console models use a different path (/inference/openai/v1) and a separate balance."
        ),
    ),
    ProviderPreset(
        key="openai_compatible",
        label="OpenAI-compatible (OpenChat, OpenRouter, LM Studio, vLLM)",
        endpoint="http://127.0.0.1:1234/v1/chat/completions",
        model="local-model",
        requires_key=False,
        note=(
            "Any server that implements /v1/chat/completions. A key is optional for local "
            "servers; a hosted endpoint with a key must use HTTPS."
        ),
    ),
)

PRESETS_BY_KEY = {preset.key: preset for preset in PROVIDERS}


@dataclass(frozen=True)
class AIConfig:
    """Non-secret AI settings loaded from the database."""

    provider: str = "disabled"
    endpoint: str = ""
    model: str = ""
    timeout: float = 60.0

    @property
    def preset(self) -> ProviderPreset:
        return PRESETS_BY_KEY.get(self.provider, PRESETS_BY_KEY["disabled"])

    @property
    def label(self) -> str:
        return self.preset.label

    @property
    def requires_key(self) -> bool:
        return self.preset.requires_key

    @property
    def enabled(self) -> bool:
        return self.provider != "disabled"


def preset_for(key: str) -> ProviderPreset:
    return PRESETS_BY_KEY.get((key or "").strip(), PRESETS_BY_KEY["disabled"])


def load_ai_config(database: "Database") -> AIConfig:
    """Load settings, migrating the pre-0.3 Ollama-only keys."""

    def setting(name: str, default: str = "") -> str:
        return (database.get_setting(name, default) or default).strip()

    provider = setting("ai.provider")
    if not provider:
        # Older builds had only ai.enabled / ai.host / ai.model, and the UI
        # implemented Ollama exclusively.
        provider = "ollama" if setting("ai.enabled") == "1" else "disabled"
    preset = preset_for(provider)
    endpoint = setting("ai.endpoint") or setting("ai.host") or preset.endpoint
    model = setting("ai.model") or preset.model
    try:
        timeout = float(setting("ai.timeout", "60") or "60")
    except ValueError:
        timeout = 60.0
    return AIConfig(
        provider=preset.key,
        endpoint=endpoint,
        model=model,
        timeout=max(5.0, min(timeout, 300.0)),
    )


def validate_endpoint(url: str, *, has_secret: bool = False) -> str:
    """Validate an AI endpoint before it is persisted or used.

    Plain HTTP is accepted for loopback and for local servers without a
    credential. A request that carries an API key must be encrypted.
    """

    value = (url or "").strip()
    if not value:
        raise ValueError("an endpoint URL is required")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("the endpoint must start with http:// or https://")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("the endpoint must include a host name")
    if parsed.scheme == "http" and has_secret and host not in LOOPBACK_HOSTS:
        raise ValueError(
            "refusing to send an API key over plain HTTP; use HTTPS or a loopback endpoint"
        )
    return value


def build_model(
    config: AIConfig,
    *,
    api_key: str = "",
    provider_name: str | None = None,
) -> AssistantModel:
    """Build a model adapter from saved (or just-edited) settings."""

    if not config.enabled:
        return NullModel()
    endpoint = validate_endpoint(config.endpoint, has_secret=bool(api_key))
    model_name = (config.model or "").strip()
    if not model_name:
        raise ValueError("a model name is required")
    preset = config.preset

    if preset.api_style == "ollama":
        if endpoint.endswith("/api/generate"):
            host = endpoint[: -len("/api/generate")]
        else:
            host = endpoint
        return OllamaModel(
            model=model_name,
            host=host,
            timeout=config.timeout,
            provider_name=provider_name or "ollama",
        )
    if preset.api_style == "anthropic":
        if not api_key:
            raise ValueError("this provider requires an API key")
        return AnthropicModel(
            model=model_name,
            endpoint=endpoint,
            api_key=api_key,
            timeout=config.timeout,
            provider_name=provider_name or "anthropic",
        )
    if preset.api_style == "gemini":
        if not api_key:
            raise ValueError("this provider requires an API key")
        return GeminiModel(
            model=model_name,
            endpoint=endpoint,
            api_key=api_key,
            timeout=config.timeout,
            provider_name=provider_name or "gemini",
        )
    if preset.requires_key and not api_key:
        raise ValueError("this provider requires an API key")
    # The OpenCode gateways (Go and Console) ask for a stable per-conversation
    # id so they can route and cache prompts.
    opencode_gateway = "opencode.ai/inference/" in endpoint
    return OpenAICompatibleModel(
        model=model_name,
        endpoint=endpoint,
        api_key=api_key,
        timeout=config.timeout,
        provider_name=provider_name or preset.key,
        token_field=(
            "max_completion_tokens" if preset.key == "openai" else "max_tokens"
        ),
        session_header="x-opencode-session" if opencode_gateway else "",
        session_id=uuid.uuid4().hex if opencode_gateway else "",
    )
