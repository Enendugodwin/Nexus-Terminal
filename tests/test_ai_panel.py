"""Assistant AI tab and Settings provider selection."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QTextCursor
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
        "opencode_go",
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


def test_a_highlighted_answer_is_inserted_exactly_as_selected(app, services):
    panel = AssistantPanel(services, compact=True)
    inserted: list[str] = []
    panel.insert_text_requested.connect(inserted.append)

    panel._ai_answered(
        ModelResponse(
            text="Here is a block:\n\n```bash\ncd /srv\nls -la\n```\n\nThat lists the folder.",
            provider="openai",
            model="gpt-test",
        )
    )
    assert panel.ai_selection_button.isEnabled() is False

    text = panel.ai_response.toPlainText()
    start = text.index("cd /srv")
    end = text.index("ls -la") + len("ls -la")
    cursor = panel.ai_response.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    panel.ai_response.setTextCursor(cursor)
    app.processEvents()

    assert panel.ai_selection_button.isEnabled() is True
    panel.insert_ai_selection()
    assert inserted == ["cd /srv\nls -la"]


def test_insert_uses_the_selection_even_without_a_selection_button(app, services):
    panel = AssistantPanel(services, compact=True)
    inserted: list[str] = []
    panel.insert_text_requested.connect(inserted.append)
    panel._ai_answered(
        ModelResponse(text="Run this:\n\n```\nsystemctl restart nginx\n```", provider="p", model="m")
    )

    text = panel.ai_response.toPlainText()
    start = text.index("systemctl")
    end = start + len("systemctl restart nginx")
    cursor = panel.ai_response.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    panel.ai_response.setTextCursor(cursor)

    panel.insert_ai_suggestion()  # the plain Insert button honours the selection
    assert inserted == ["systemctl restart nginx"]


def test_a_fenced_command_beats_the_surrounding_prose(app, services):
    """The old guess took the first command-like line, which was often wrong."""

    panel = AssistantPanel(services, compact=True)
    inserted: list[str] = []
    panel.send_shell_requested.connect(inserted.append)
    panel._ai_answered(
        ModelResponse(
            text=(
                "You could try df -h to see the space, but the real fix is:\n\n"
                "```bash\nsudo journalctl --vacuum-size=200M\n```\n"
            ),
            provider="p",
            model="m",
        )
    )
    panel.insert_ai_suggestion()
    assert inserted == ["sudo journalctl --vacuum-size=200M"]


def test_the_prompt_carries_the_open_terminal_context(app, services, monkeypatch):
    panel = AssistantPanel(services, compact=True)
    panel.set_context_provider(
        lambda: "Shell: bash\nWorking directory: /srv\nRecent terminal output:\n$ df -h"
    )
    captured: list[str] = []

    def fake_async(fn, *args, on_result=None, on_error=None, **kwargs):
        result = fn(*args, **kwargs)
        if on_result is not None:
            on_result(result)

    monkeypatch.setattr("nexus_remote.ui.assistant_panel.run_async", fake_async)
    monkeypatch.setattr(services.models, "available", lambda: True)
    monkeypatch.setattr(
        services.models,
        "complete",
        lambda prompt, system=None: captured.append(prompt)
        or ModelResponse(text="ok", provider="p", model="m"),
    )

    panel.ai_question.setPlainText("Why is the disk full?")
    panel.ask_ai()
    assert captured
    assert "Working directory: /srv" in captured[0]
    assert "Included" in panel.ai_context_note.text() or "Sent with" in panel.ai_context_note.text()

    panel.ai_context_toggle.setChecked(False)
    panel.ask_ai()
    assert "Working directory: /srv" not in captured[-1]
    assert "Terminal context is off" in panel.ai_context_note.text()



def test_a_cut_off_answer_says_so(app, services):
    panel = AssistantPanel(services, compact=True)
    panel._ai_answered(
        ModelResponse(text="partial answer", provider="p", model="m", truncated=True)
    )
    assert "cut off" in panel.ai_response_header.text()
    assert "partial answer" in panel.ai_response.toPlainText()


def test_a_stale_answer_cannot_overwrite_a_newer_question(app, services):
    """Two overlapping questions must not interleave their answers."""

    panel = AssistantPanel(services, compact=True)
    panel._ai_request_id = 8

    panel._ai_answered(
        ModelResponse(text="answer to the old question", provider="p", model="m"),
        7,
    )

    assert panel.ai_response.toPlainText() == ""
    assert panel._ai_last_response is None

    panel._ai_answered(
        ModelResponse(text="answer to the current question", provider="p", model="m"),
        8,
    )
    assert panel.ai_response.toPlainText() == "answer to the current question"


def test_diagnosing_a_full_scrollback_keeps_only_the_tail(app, services):
    from nexus_remote.ui.assistant_panel import MAX_DIAGNOSIS_LINES

    panel = AssistantPanel(services, compact=True)
    panel.diagnose_text("\n".join(f"line {index}" for index in range(500)))

    kept = panel.error_input.toPlainText().splitlines()
    assert 0 < len(kept) <= MAX_DIAGNOSIS_LINES
    assert kept[-1] == "line 499"
