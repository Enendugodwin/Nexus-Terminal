"""Inline (ghost) completion in the console and the assistant prefix box."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nexus_remote.ui.assistant_panel import AssistantPanel
from nexus_remote.ui.command_input import SuggestionLineEdit
from nexus_remote.ui.workspace import WorkspacePage


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture()
def editor(app) -> SuggestionLineEdit:
    widget = SuggestionLineEdit()
    widget.setMinimumHeight(34)
    yield widget
    widget.deleteLater()


# -- the widget -------------------------------------------------------------
def test_ghost_text_previews_the_remainder(editor):
    editor.setText("syst")
    editor.setCursorPosition(4)
    editor.set_suggestion("systemctl status nginx")

    assert editor.ghost() == "systemctl status nginx"
    assert editor.suggestion_text() == "emctl status nginx"
    assert editor.text() == "syst"  # the line itself is untouched


def test_ghost_is_only_offered_for_a_prefix_completion(editor):
    editor.setText("status")
    editor.set_suggestion("systemctl status nginx")  # matches, but not a prefix

    assert editor.ghost() == ""
    assert editor.suggestion_text() == ""


def test_ghost_is_hidden_when_the_cursor_is_not_at_the_end(editor):
    editor.setText("syst")
    editor.setCursorPosition(2)
    editor.set_suggestion("systemctl status nginx")

    assert editor.ghost() == ""


def test_ghost_is_hidden_with_a_selection(editor):
    editor.setText("syst")
    editor.selectAll()
    editor.set_suggestion("systemctl status nginx")

    assert editor.ghost() == ""


def test_identical_text_is_not_a_suggestion(editor):
    editor.setText("pwd")
    editor.set_suggestion("pwd")

    assert editor.ghost() == ""


def test_typing_past_the_suggestion_clears_it(editor):
    editor.setText("sy")
    editor.setCursorPosition(2)
    editor.set_suggestion("systemctl")
    assert editor.ghost() == "systemctl"

    editor.setText("systemctl")  # what typing the rest would do
    assert editor.ghost() == ""


def test_tab_accepts_and_announces_the_completion(editor):
    accepted: list[str] = []
    editor.accepted.connect(accepted.append)
    editor.setText("df")
    editor.setCursorPosition(2)
    editor.set_suggestion("df -h")

    QTest.keyClick(editor, Qt.Key.Key_Tab)

    assert editor.text() == "df -h"
    assert editor.cursorPosition() == len("df -h")
    assert editor.ghost() == ""
    assert accepted == ["df -h"]


def test_right_arrow_accepts_at_the_end_only(editor):
    editor.setText("df")
    editor.setCursorPosition(2)
    editor.set_suggestion("df -h")

    QTest.keyClick(editor, Qt.Key.Key_Right)
    assert editor.text() == "df -h"

    editor.setText("df")
    editor.setCursorPosition(0)
    editor.set_suggestion("df -h")
    QTest.keyClick(editor, Qt.Key.Key_Right)
    assert editor.text() == "df"
    assert editor.cursorPosition() == 1


def test_escape_dismisses_without_changing_the_line(editor):
    dismissed: list[bool] = []
    editor.dismissed.connect(lambda: dismissed.append(True))
    editor.setText("df")
    editor.setCursorPosition(2)
    editor.set_suggestion("df -h")

    QTest.keyClick(editor, Qt.Key.Key_Escape)

    assert editor.text() == "df"
    assert editor.ghost() == ""
    assert dismissed == [True]


def test_arrow_keys_are_reported_for_list_navigation(editor):
    moves: list[int] = []
    editor.navigate.connect(moves.append)

    QTest.keyClick(editor, Qt.Key.Key_Down)
    QTest.keyClick(editor, Qt.Key.Key_Up)

    assert moves == [1, -1]
    assert editor.text() == ""


def test_a_suggestion_paints_without_errors(editor):
    editor.setText("df")
    editor.setCursorPosition(2)
    editor.set_suggestion("df -h")
    editor.resize(320, 34)

    pixmap = editor.grab()

    assert not pixmap.isNull()
    assert pixmap.width() > 0


def test_plain_editing_still_works_without_a_suggestion(editor):
    editor.setFocus()
    QTest.keyClicks(editor, "ls")
    QTest.keyClick(editor, Qt.Key.Key_Left)
    QTest.keyClicks(editor, "a")

    assert editor.text() == "las"


# -- console tab ------------------------------------------------------------
def test_console_previews_and_accepts_the_top_suggestion(app, services):
    workspace = WorkspacePage(services)
    workspace.tabs.setCurrentWidget(workspace.tabs.widget(2))  # SSH console
    workspace.resize(900, 600)
    workspace.show()
    workspace.console_environment.setCurrentIndex(workspace.console_environment.findData("bash"))

    workspace.console_input.setText("df")
    workspace.console_input.setCursorPosition(2)
    workspace._update_completions("df")

    expected = workspace.services.autocomplete.suggest("df", "bash")[0].text
    assert workspace.completion_list.count() > 0
    assert workspace.console_input.ghost() == expected

    QTest.keyClick(workspace.console_input, Qt.Key.Key_Tab)

    assert workspace.console_input.text() == expected
    assert workspace.completion_list.currentRow() == -1


def test_console_enter_fills_a_highlighted_candidate_before_running(app, services):
    workspace = WorkspacePage(services)
    workspace.console_environment.setCurrentIndex(workspace.console_environment.findData("bash"))
    workspace.console_input.setText("df")
    workspace.console_input.setCursorPosition(2)
    workspace._update_completions("df")
    workspace._navigate_completions(1)
    highlighted = str(workspace.completion_list.currentItem().data(Qt.ItemDataRole.UserRole))

    calls: list[bool] = []
    workspace.run_command = lambda: calls.append(True)  # type: ignore[method-assign]

    workspace._on_console_return()
    assert workspace.console_input.text() == highlighted
    assert calls == []

    workspace._on_console_return()
    assert calls == [True]


def test_console_navigation_wraps_and_previews(app, services):
    workspace = WorkspacePage(services)
    workspace.console_environment.setCurrentIndex(workspace.console_environment.findData("bash"))
    workspace.console_input.setText("df")
    workspace.console_input.setCursorPosition(2)
    workspace._update_completions("df")
    total = workspace.completion_list.count()
    assert total >= 2

    # The first press highlights the first candidate, so after ``total`` presses
    # the last one is highlighted; the next press wraps to the top.
    for _ in range(total):
        workspace._navigate_completions(1)
    assert workspace.completion_list.currentRow() == total - 1

    workspace._navigate_completions(1)
    assert workspace.completion_list.currentRow() == 0

    workspace._navigate_completions(-1)
    assert workspace.completion_list.currentRow() == total - 1


def test_console_hint_documents_the_keys(app, services):
    workspace = WorkspacePage(services)

    hint = workspace.completion_hint.text()
    assert "Tab" in hint
    assert "Enter" in hint
    assert "Esc" in hint
    assert workspace.console_input.minimumHeight() >= 34


# -- assistant prefix box ---------------------------------------------------
def test_prefix_box_is_comfortably_sized(app, services):
    panel = AssistantPanel(services)

    assert panel.prefix_edit.minimumHeight() >= 36
    assert panel.prefix_edit.minimumWidth() >= 180
    assert panel.prefix_edit.isClearButtonEnabled() is True


def test_suggestion_list_is_roomy(app, services):
    panel = AssistantPanel(services)

    assert panel.suggestion_list.minimumHeight() >= 240
    assert panel.explanation_view.maximumHeight() <= 120


def test_compact_suggestion_list_is_still_roomy(app, services):
    panel = AssistantPanel(services, compact=True)

    assert panel.suggestion_list.minimumHeight() >= 200
    assert panel.prefix_edit.minimumHeight() >= 36


def test_prefix_box_completes_inline(app, services):
    panel = AssistantPanel(services)
    panel.resize(420, 600)
    panel.show()
    panel.prefix_edit.setText("df")
    panel.prefix_edit.setCursorPosition(2)
    panel._update_suggestions()

    expected = services.autocomplete.suggest("df", "bash")[0].text
    assert panel.prefix_edit.ghost() == expected

    QTest.keyClick(panel.prefix_edit, Qt.Key.Key_Tab)

    assert panel.prefix_edit.text() == expected


def test_prefix_box_enter_takes_the_highlighted_suggestion(app, services):
    panel = AssistantPanel(services)
    panel.prefix_edit.setText("df")
    panel.prefix_edit.setCursorPosition(2)
    panel._update_suggestions()
    panel._navigate_suggestions(1)
    highlighted = str(panel.suggestion_list.currentItem().data(Qt.ItemDataRole.UserRole))

    QTest.keyClick(panel.prefix_edit, Qt.Key.Key_Return)

    assert panel.prefix_edit.text() == highlighted
    assert panel.suggestion_list.currentRow() == -1


# -- prefix actions ---------------------------------------------------------
def test_prefix_actions_are_disabled_until_something_is_typed(app, services):
    panel = AssistantPanel(services)

    assert panel.send_shell_button.isEnabled() is False
    assert panel.copy_button.isEnabled() is False

    panel.prefix_edit.setText("df -h")

    assert panel.send_shell_button.isEnabled() is True
    assert panel.copy_button.isEnabled() is True


def test_prefix_copy_puts_the_command_on_the_clipboard(app, services):
    panel = AssistantPanel(services)
    messages: list[str] = []
    panel.status_message.connect(messages.append)
    panel.prefix_edit.setText("  df -h  ")

    panel.copy_prefix()

    assert QApplication.clipboard().text() == "df -h"
    assert messages == ["Command copied to the clipboard."]


def test_prefix_send_emits_the_command_without_running_it(app, services):
    panel = AssistantPanel(services)
    sent: list[str] = []
    panel.send_shell_requested.connect(sent.append)
    panel.prefix_edit.setText("uptime")

    panel.send_to_shell()

    assert sent == ["uptime"]
    assert services.history.recent() == []


def test_empty_prefix_actions_are_no_ops(app, services):
    panel = AssistantPanel(services)
    sent: list[str] = []
    panel.send_shell_requested.connect(sent.append)

    panel.send_to_shell()
    panel.copy_prefix()

    assert sent == []


class _FakeShell:
    """Minimal write/read/resize channel for the terminal view."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data) -> None:
        self.written.append(data if isinstance(data, bytes) else data.encode())

    def read(self, timeout: float = 0.0, max_bytes: int = 65536) -> bytes:
        return b""

    def resize(self, rows: int, columns: int) -> None:
        pass

    def close(self) -> None:
        pass


def test_send_to_open_shell_types_without_pressing_enter(app, services):
    workspace = WorkspacePage(services)
    shell = _FakeShell()
    workspace.terminal_view.attach(shell, session_id="session-1", label="fake")
    messages: list[str] = []
    workspace.status_message.connect(messages.append)

    workspace._send_to_open_shell("systemctl status nginx")

    written = b"".join(shell.written).decode()
    assert written == "systemctl status nginx"
    assert "\r" not in written and "\n" not in written
    assert workspace.tabs.currentWidget() is workspace.terminal_page
    assert messages and "press Enter" in messages[0]
    workspace.terminal_view.detach()


def test_send_without_a_shell_falls_back_to_the_console(app, services):
    workspace = WorkspacePage(services)
    messages: list[str] = []
    workspace.status_message.connect(messages.append)

    workspace._send_to_open_shell("uptime")

    assert workspace.tabs.currentWidget() is workspace.console_page
    assert workspace.console_input.text() == "uptime"
    assert messages and "SSH console instead" in messages[0]
