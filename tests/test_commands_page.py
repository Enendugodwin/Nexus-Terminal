"""Saved commands page, editor dialog and console hand-off."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from nexus_remote.ui.commands_page import CommandDialog, CommandsPage
from nexus_remote.ui.main_window import PAGES
from nexus_remote.ui.workspace import WorkspacePage


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture()
def page(app, services) -> CommandsPage:
    widget = CommandsPage(services)
    yield widget
    widget.deleteLater()


# -- the library page -------------------------------------------------------
def test_page_lists_saved_commands(page, services):
    services.snippets.add(
        "Restart nginx", "systemctl restart nginx", environment="bash", tags=["web"]
    )

    page.refresh()

    assert page.table.rowCount() == 1
    assert page.table.item(0, 0).text() == "Restart nginx"
    assert page.table.item(0, 1).text() == "systemctl restart nginx"
    assert "1 saved command" in page.status_label.text()


def test_page_searches_names_commands_and_tags(page, services):
    services.snippets.add("restart", "systemctl restart nginx", environment="bash", tags=["web"])
    services.snippets.add("processes", "Get-Process", environment="powershell", tags=["win"])

    page.refresh()
    assert page.table.rowCount() == 2

    page.search_edit.setText("get-process")
    assert page.table.rowCount() == 1
    assert page.table.item(0, 0).text() == "processes"

    page.search_edit.setText("web")
    assert page.table.rowCount() == 1
    assert page.table.item(0, 0).text() == "restart"


def test_page_filters_by_environment(page, services):
    services.snippets.add("restart", "systemctl restart nginx", environment="bash")
    services.snippets.add("processes", "Get-Process", environment="powershell")

    page.refresh()
    page.environment_filter.setCurrentIndex(page.environment_filter.findData("bash"))

    assert page.table.rowCount() == 1
    assert page.table.item(0, 2).text() == "bash"


def test_selected_command_is_sent_to_the_console(page, services):
    services.snippets.add("uptime", "uptime", environment="bash")
    page.refresh()
    page.table.selectRow(0)
    received: list[tuple[str, str]] = []
    page.send_requested.connect(lambda command, environment: received.append((command, environment)))

    page.send_selected()

    assert received == [("uptime", "bash")]
    assert "SSH console" in page.status_label.text()


def test_sending_without_a_selection_is_harmless(page):
    received: list[tuple[str, str]] = []
    page.send_requested.connect(lambda command, environment: received.append((command, environment)))

    page.send_selected()

    assert received == []
    assert "Select a command" in page.status_label.text()


def test_delete_asks_first(page, services, monkeypatch):
    services.snippets.add("uptime", "uptime", environment="bash")
    page.refresh()
    page.table.selectRow(0)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )

    page.delete_selected()

    assert services.snippets.list() == []
    assert page.table.rowCount() == 0


def test_delete_keeps_the_command_when_declined(page, services, monkeypatch):
    services.snippets.add("uptime", "uptime", environment="bash")
    page.refresh()
    page.table.selectRow(0)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No
    )

    page.delete_selected()

    assert len(services.snippets.list()) == 1


def test_copy_puts_the_command_on_the_clipboard(page, services, app):
    services.snippets.add("uptime", "uptime -p", environment="bash")
    page.refresh()
    page.table.selectRow(0)

    page.copy_selected()

    assert QApplication.clipboard().text() == "uptime -p"


def test_page_note_states_nothing_runs_automatically(page):
    texts = " ".join(
        child.text() for child in page.findChildren(type(page.status_label)) if hasattr(child, "text")
    )
    assert "nothing runs until you press Run" in texts


# -- the editor dialog ------------------------------------------------------
def test_new_command_is_saved_with_policy_risk(app, services):
    dialog = CommandDialog(services, command="uptime", environment="bash")
    dialog.name_edit.setText("Check uptime")
    dialog.tags_edit.setText("health, linux, health")
    dialog.description_edit.setPlainText("Quick health check")

    dialog.accept()

    assert dialog.saved is not None
    saved = services.snippets.get(dialog.saved.id)
    assert saved is not None
    assert saved.name == "Check uptime"
    assert saved.command == "uptime"
    assert saved.environment == "bash"
    assert saved.risk == "read-only"
    assert saved.tags == ["health", "linux"]
    assert saved.verified is False


def test_destructive_command_is_classified(app, services):
    dialog = CommandDialog(services, command="rm -rf /var/tmp/x", environment="bash")

    dialog.accept()

    assert dialog.saved is not None
    assert services.snippets.get(dialog.saved.id).risk == "destructive"


def test_name_defaults_to_the_command(app, services):
    dialog = CommandDialog(services, command="df -h", environment="bash")

    dialog.accept()

    assert dialog.saved is not None
    assert dialog.saved.name == "df -h"


def test_empty_command_is_refused(app, services, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *args, **kwargs: warnings.append(text)
    )
    dialog = CommandDialog(services)

    dialog.accept()

    assert dialog.saved is None
    assert services.snippets.list() == []
    assert warnings and "needs a command" in warnings[0]


def test_editing_updates_the_same_snippet(app, services):
    snippet = services.snippets.add("old", "uptime", environment="bash", tags=["a"])
    dialog = CommandDialog(services, snippet)
    dialog.name_edit.setText("new name")
    dialog.command_edit.setText("rm -rf /tmp/cache")
    dialog.tags_edit.setText("b")

    dialog.accept()

    assert dialog.saved is not None
    assert dialog.saved.id == snippet.id
    reloaded = services.snippets.get(snippet.id)
    assert reloaded.name == "new name"
    assert reloaded.command == "rm -rf /tmp/cache"
    assert reloaded.risk == "destructive"
    assert reloaded.tags == ["b"]
    assert len(services.snippets.list()) == 1


def test_dialog_prefills_every_field(app, services):
    snippet = services.snippets.add(
        "logs",
        "tail -f /var/log/syslog",
        environment="bash",
        description="watch logs",
        tags=["logs"],
        verified=True,
    )

    dialog = CommandDialog(services, snippet)

    assert dialog.name_edit.text() == "logs"
    assert dialog.command_edit.text() == "tail -f /var/log/syslog"
    assert dialog.environment_combo.currentData() == "bash"
    assert dialog.description_edit.toPlainText() == "watch logs"
    assert dialog.tags_edit.text() == "logs"
    assert dialog.verified_check.isChecked() is True


def test_dialog_previews_completions_inline(app, services):
    dialog = CommandDialog(services, command="df", environment="bash")
    dialog.command_edit.setCursorPosition(2)

    dialog._suggest()

    expected = services.autocomplete.suggest("df", "bash")[0].text
    assert dialog.command_edit.ghost() == expected


def test_dialog_reports_risk_while_typing(app, services):
    dialog = CommandDialog(services, environment="bash")
    dialog.command_edit.setText("rm -rf /data")
    dialog._refresh_risk()

    assert "destructive" in dialog.risk_label.text()


# -- wiring -----------------------------------------------------------------
def test_commands_page_is_in_the_sidebar(app):
    names = [name for name, _description in PAGES]
    assert "Commands" in names


def test_workspace_loads_a_saved_command_without_running_it(app, services):
    workspace = WorkspacePage(services)

    workspace.load_command("systemctl restart nginx", "bash")

    assert workspace.tabs.currentWidget() is workspace.console_page
    assert workspace.console_input.text() == "systemctl restart nginx"
    assert workspace.console_environment.currentData() == "bash"


def test_workspace_console_offers_a_save_button(app, services):
    workspace = WorkspacePage(services)

    assert workspace.save_command_button.isEnabled()
    assert "Commands" in workspace.save_command_button.toolTip()
