"""Assistant AI tab and Settings provider selection."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from nexus_remote.assistant.model_adapters import ModelResponse
from nexus_remote.ui.assistant_panel import AssistantPanel
from nexus_remote.ui.settings_view import SettingsPage


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_disabled_ai_says_the_offline_features_still_work(app, services):
    panel = AssistantPanel(services, compact=True)
    assert "AI is disabled" in panel.ai_status.text()
    assert "offline" in panel.ai_status.text()
    assert panel.ai_ask_button.isEnabled()


def test_answered_response_is_labelled_and_can_be_inserted(app, services):
    panel = AssistantPanel(services, compact=True)
    inserted: list[str] = []
    panel.send_shell_requested.connect(inserted.append)

    panel._ai_answered(
        ModelResponse(
            text="Use this read-only check:\ndf -h",
            provider="openai",
            model="gpt-test",
        )
    )

    assert "unverified" in panel.ai_response_header.text()
    assert "df -h" in panel.ai_response.toPlainText()
    assert panel.ai_insert_button.isEnabled()
    panel.insert_ai_suggestion()
    assert inserted == ["df -h"]


def test_empty_response_is_reported_without_crashing(app, services):
    panel = AssistantPanel(services, compact=True)
    panel._ai_answered(ModelResponse(text="", provider="openai", model="gpt-test"))
    assert "did not answer" in panel.ai_response_header.text()
    assert panel._ai_last_response is None


def test_removing_a_stored_key_leaves_the_provider_not_ready(app, services):
    services.save_ai_api_key("openai", "sk-test")
    services.database.set_setting("ai.provider", "openai")
    services.database.set_setting("ai.endpoint", "https://api.openai.com/v1/chat/completions")
    services.database.set_setting("ai.model", "gpt-test")
    services.configure_ai_from_settings()

    page = SettingsPage(services)
    page.refresh()
    assert page.ai_clear_key.isEnabled() is True
    page.ai_clear_key.setChecked(True)

    assert page._save_ai_settings() is True
    assert services.ai_api_key("openai") == ""
    assert "No key is stored" in page.ai_key_status.text()
    assert services.models.model.name == "disabled"


def test_settings_offers_hosted_and_local_providers(app, services):
    page = SettingsPage(services)
    values = [page.ai_provider.itemData(index) for index in range(page.ai_provider.count())]
    assert values == [
        "disabled",
        "ollama",
        "openai",
        "anthropic",
        "gemini",
        "openai_compatible",
    ]


def test_choosing_a_provider_loads_its_defaults(app, services):
    page = SettingsPage(services)
    page.ai_provider.setCurrentIndex(page.ai_provider.findData("anthropic"))

    assert page.ai_endpoint.text() == "https://api.anthropic.com/v1/messages"
    assert page.ai_model.text() == "claude-sonnet-4-5"
    assert "No key is stored" in page.ai_key_status.text()

    page.ai_provider.setCurrentIndex(page.ai_provider.findData("ollama"))
    assert page.ai_endpoint.text().startswith("http://127.0.0.1")
    assert page.ai_clear_key.isEnabled() is False
