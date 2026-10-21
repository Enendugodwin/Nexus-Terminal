"""Optional AI providers: validation, request shaping and vault-backed keys."""

from __future__ import annotations

import io
import urllib.error

import pytest

from nexus_remote.assistant import model_adapters
from nexus_remote.assistant.model_adapters import (
    AnthropicModel,
    GeminiModel,
    NullModel,
    OpenAICompatibleModel,
)
from nexus_remote.assistant.providers import (
    AIConfig,
    build_model,
    load_ai_config,
    preset_for,
    validate_endpoint,
)


def test_provider_catalogue_covers_hosted_and_local_options():
    assert preset_for("openai").requires_key is True
    assert preset_for("anthropic").requires_key is True
    assert preset_for("gemini").requires_key is True
    assert preset_for("ollama").requires_key is False
    assert preset_for("openai_compatible").requires_key is False
    assert preset_for("nonsense").key == "disabled"


def test_build_model_selects_the_right_adapter():
    openai = build_model(
        AIConfig("openai", "https://api.openai.com/v1/chat/completions", "gpt-test"),
        api_key="sk-test",
    )
    anthropic = build_model(
        AIConfig("anthropic", "https://api.anthropic.com/v1/messages", "claude-test"),
        api_key="sk-ant",
    )
    gemini = build_model(
        AIConfig(
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            "gemini-test",
        ),
        api_key="goog",
    )
    assert isinstance(openai, OpenAICompatibleModel) and openai.available()
    assert openai.token_field == "max_completion_tokens"
    assert isinstance(anthropic, AnthropicModel) and anthropic.available()
    assert isinstance(gemini, GeminiModel) and gemini.available()
    assert "{model}" not in gemini.endpoint
    assert "gemini-test" in gemini.endpoint
    assert isinstance(build_model(AIConfig(provider="disabled")), NullModel)


def test_hosted_provider_cannot_be_built_without_a_key():
    with pytest.raises(ValueError, match="API key"):
        build_model(
            AIConfig("openai", "https://api.openai.com/v1/chat/completions", "gpt-test")
        )


def test_a_key_is_never_sent_over_plain_http():
    with pytest.raises(ValueError, match="plain HTTP"):
        validate_endpoint("http://api.example.com/v1/chat/completions", has_secret=True)
    assert validate_endpoint("http://127.0.0.1:1234/v1/chat/completions", has_secret=True)
    assert validate_endpoint("http://inference.lan:8080/v1", has_secret=False)
    with pytest.raises(ValueError):
        validate_endpoint("file:///tmp/model", has_secret=False)


def test_openai_compatible_request_and_response(monkeypatch):
    captured = {}

    def fake_request(url, **kwargs):
        captured.update(url=url, **kwargs)
        return {"choices": [{"message": {"content": "read-only check: df -h"}}]}

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)
    model = OpenAICompatibleModel(
        "gpt-test",
        "https://api.openai.com/v1/chat/completions",
        "sk-secret",
        provider_name="openai",
    )
    response = model.complete("What should I check?", system="Be cautious.")

    assert response.text == "read-only check: df -h"
    assert response.provider == "openai"
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-secret"
    assert captured["payload"]["messages"] == [
        {"role": "system", "content": "Be cautious."},
        {"role": "user", "content": "What should I check?"},
    ]
    assert captured["payload"]["max_tokens"] == 4000
    assert captured["payload"]["stream"] is False


def test_anthropic_request_and_content_parts(monkeypatch):
    captured = {}

    def fake_request(url, **kwargs):
        captured.update(url=url, **kwargs)
        return {"content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]}

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)
    model = AnthropicModel("claude-test", "https://api.anthropic.com/v1/messages", "sk-ant")
    response = model.complete("hello", system="system prompt")

    assert response.text == "first\nsecond"
    assert captured["headers"]["x-api-key"] == "sk-ant"
    assert captured["headers"]["anthropic-version"]
    assert captured["payload"]["system"] == "system prompt"
    assert captured["payload"]["messages"][0]["role"] == "user"


def test_gemini_request_and_response(monkeypatch):
    captured = {}

    def fake_request(url, **kwargs):
        captured.update(url=url, **kwargs)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)
    model = GeminiModel(
        "gemini-test",
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "goog",
    )
    response = model.complete("hello", system="be safe")

    assert response.text == "ok"
    assert "gemini-test" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "goog"
    assert captured["payload"]["systemInstruction"]["parts"][0]["text"] == "be safe"


def test_legacy_ollama_settings_are_migrated(database):
    database.set_setting("ai.enabled", "1")
    database.set_setting("ai.host", "http://127.0.0.1:11434")
    database.set_setting("ai.model", "llama3.1")

    config = load_ai_config(database)

    assert config.provider == "ollama"
    assert config.enabled is True
    assert config.endpoint.startswith("http://127.0.0.1")


def test_api_key_is_stored_in_the_vault_not_the_database(services):
    services.save_ai_api_key("openai", "sk-test-secret")
    assert services.ai_api_key("openai") == "sk-test-secret"
    assert services.ai_key_configured("openai") is True

    rows = services.database.query("SELECT * FROM settings")
    assert "sk-test-secret" not in str(rows)

    services.database.set_setting("ai.provider", "openai")
    services.database.set_setting("ai.endpoint", "https://api.openai.com/v1/chat/completions")
    services.database.set_setting("ai.model", "gpt-test")
    services.configure_ai_from_settings()

    assert services.models.model.name == "openai"
    assert services.models.available() is True

    services.clear_ai_api_key("openai")
    assert services.ai_api_key("openai") == ""


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, *_args: int) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def test_requests_identify_the_app_instead_of_python_urllib(monkeypatch):
    """Bot protection rejects the default urllib agent (Cloudflare error 1010)."""

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        return _FakeResponse(b'{"choices": [{"message": {"content": "ok"}}]}')

    monkeypatch.setattr(model_adapters.urllib.request, "urlopen", fake_urlopen)
    model = OpenAICompatibleModel("m", "https://example.test/v1/chat/completions", "k")

    assert model.complete("hello").text == "ok"
    agent = captured["headers"]["user-agent"]
    assert "python-urllib" not in agent.lower()
    assert "nexus" in agent.lower()


def test_a_refused_provider_reports_its_own_reason(monkeypatch):
    """The Settings test must show why the provider said no, not "no answer"."""

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO(
                b'{"error": {"message": "free tier can only be used inside the vendor app"}}'
            ),
        )

    monkeypatch.setattr(model_adapters.urllib.request, "urlopen", fake_urlopen)
    model = OpenAICompatibleModel("m", "https://example.test/v1/chat/completions", "k")

    with pytest.raises(model_adapters.ModelUnavailable) as excinfo:
        model.probe()
    message = str(excinfo.value)
    assert "403" in message
    assert "free tier can only be used" in message


def test_the_provider_test_uses_the_completion_route(monkeypatch):
    """A model list can succeed while completions are blocked, so test the real route."""

    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs.get("method", "POST")))
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)
    model = OpenAICompatibleModel("m", "https://example.test/v1/chat/completions", "k")

    assert model.probe() is True
    assert calls == [("https://example.test/v1/chat/completions", "POST")]
    # The real request budget is restored after the minimal test request.
    assert model.max_tokens == 4000


def test_the_anthropic_provider_test_reports_a_refusal(monkeypatch):
    def fake_request(url, **kwargs):
        raise model_adapters.ModelUnavailable("Anthropic returned HTTP 401: invalid x-api-key")

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)
    model = AnthropicModel("claude-test", api_key="bad")

    with pytest.raises(model_adapters.ModelUnavailable) as excinfo:
        model.probe()
    assert "invalid x-api-key" in str(excinfo.value)
    assert model.max_tokens == 4000


def test_a_disabled_model_refuses_to_be_tested():
    with pytest.raises(model_adapters.ModelUnavailable, match="disabled"):
        NullModel().probe()


def test_an_optional_provider_still_sends_its_stored_key(services, monkeypatch):
    """OpenAI-compatible endpoints accept a key that is merely optional.

    The key must not be dropped just because a local server would not need one:
    a hosted compatible endpoint then answers ``401 Missing API key``.
    """

    services.save_ai_api_key("openai_compatible", "sk-compatible")
    services.database.set_setting("ai.provider", "openai_compatible")
    services.database.set_setting(
        "ai.endpoint", "https://gateway.example.test/v1/chat/completions"
    )
    services.database.set_setting("ai.model", "test-model")
    services.configure_ai_from_settings()

    captured = {}

    def fake_request(url, **kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)

    assert services.models.available() is True
    assert services.models.complete("hello").text == "ok"
    assert captured["headers"]["Authorization"] == "Bearer sk-compatible"


def test_a_local_compatible_endpoint_works_without_any_key(services, monkeypatch):
    services.database.set_setting("ai.provider", "openai_compatible")
    services.database.set_setting("ai.endpoint", "http://127.0.0.1:1234/v1/chat/completions")
    services.database.set_setting("ai.model", "local-model")
    services.configure_ai_from_settings()

    captured = {}

    def fake_request(url, **kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)

    assert services.models.complete("hello").text == "ok"
    assert "Authorization" not in captured["headers"]


def test_opencode_go_is_a_first_class_provider():
    """The Go subscription and the Console balance are different endpoints."""

    preset = preset_for("opencode_go")
    assert preset.label.startswith("OpenCode Go")
    assert preset.requires_key is True
    assert "/inference/go/openai/v1/chat/completions" in preset.endpoint
    assert preset.model


def test_the_opencode_gateway_gets_a_stable_session_header():
    model = build_model(
        AIConfig(
            "opencode_go",
            "https://opencode.ai/inference/go/openai/v1/chat/completions",
            "deepseek-v4.1-flash",
        ),
        api_key="go-key",
    )
    assert model.session_header == "x-opencode-session"
    assert model.session_id
    headers = model._headers()
    assert headers["Authorization"] == "Bearer go-key"
    assert headers["x-opencode-session"] == model.session_id


def test_other_providers_do_not_send_the_opencode_session_header():
    model = build_model(
        AIConfig("openai", "https://api.openai.com/v1/chat/completions", "gpt-test"),
        api_key="sk-test",
    )
    assert model.session_header == ""
    assert "x-opencode-session" not in model._headers()


def test_a_reasoning_only_reply_is_retried_with_a_bigger_budget(monkeypatch):
    """Reasoning tokens share the completion budget and can starve the answer."""

    budgets = []

    def fake_request(url, **kwargs):
        budgets.append(kwargs["payload"]["max_tokens"])
        if len(budgets) == 1:
            return {
                "choices": [
                    {
                        "message": {"content": None, "reasoning_content": "thinking..."},
                        "finish_reason": "length",
                    }
                ]
            }
        return {"choices": [{"message": {"content": "the answer"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)
    model = OpenAICompatibleModel("m", "https://example.test/v1/chat/completions", "k")

    response = model.complete("a hard question")

    assert response.text == "the answer"
    assert response.truncated is False
    assert budgets == [4000, 8000]


def test_an_empty_reply_after_the_retry_explains_the_reasoning_budget(monkeypatch):
    def fake_request(url, **kwargs):
        return {
            "choices": [
                {
                    "message": {"content": None, "reasoning_content": "still thinking..."},
                    "finish_reason": "length",
                }
            ]
        }

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)
    model = OpenAICompatibleModel("m", "https://example.test/v1/chat/completions", "k")

    with pytest.raises(model_adapters.ModelUnavailable) as excinfo:
        model.complete("a very hard question")
    assert "reasoning" in str(excinfo.value)


def test_a_cut_off_answer_is_flagged(monkeypatch):
    def fake_request(url, **kwargs):
        return {"choices": [{"message": {"content": "partial answer"}, "finish_reason": "length"}]}

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)
    model = OpenAICompatibleModel("m", "https://example.test/v1/chat/completions", "k")

    response = model.complete("long question")
    assert response.text == "partial answer"
    assert response.truncated is True


def test_the_retry_is_skipped_when_the_first_call_was_slow(monkeypatch):
    calls = []

    def fake_request(url, **kwargs):
        calls.append(kwargs["payload"]["max_tokens"])
        return {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}

    monkeypatch.setattr(model_adapters, "_request_json", fake_request)
    monkeypatch.setattr(model_adapters, "REASONING_RETRY_AFTER_SECONDS", -1.0)
    model = OpenAICompatibleModel("m", "https://example.test/v1/chat/completions", "k")

    with pytest.raises(model_adapters.ModelUnavailable):
        model.complete("slow question")
    assert calls == [4000], "a slow first call must not be retried"



def test_the_go_preset_leaves_room_for_reasoning_tokens():
    """Reasoning models spend their thinking from the same completion budget."""

    preset = preset_for("opencode_go")
    assert preset.max_tokens >= 8000
    model = build_model(
        AIConfig("opencode_go", preset.endpoint, "deepseek-v4.1-flash"), api_key="k"
    )
    assert model.max_tokens == preset.max_tokens

