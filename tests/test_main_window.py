"""Main window: the Workspace menu must actually carry its actions."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from nexus_remote.ui.main_window import PAGE_INDEX, MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _window(services) -> MainWindow:
    window = MainWindow(services)
    window.show()
    return window


def test_workspace_menu_keeps_all_of_its_actions(app, services):
    window = _window(services)
    button = next(
        child for child in window.findChildren(QPushButton) if child.text() == "Workspace menu"
    )
    menu = button.menu()

    assert menu is not None
    assert [action.text() for action in menu.actions()] == [
        "New connection",
        "Assistant beside terminal",
        "Saved commands",
        "Run diagnostics",
        "Security Center",
        "Settings",
        "",
        "Exit",
    ]
    assert menu.actions()[6].isSeparator()
    window.close()


def test_workspace_menu_actions_navigate(app, services):
    window = _window(services)
    menu = next(
        child.menu()
        for child in window.findChildren(QPushButton)
        if child.text() == "Workspace menu"
    )
    actions = {action.text(): action for action in menu.actions() if action.text()}

    actions["Saved commands"].trigger()
    assert window.stack.currentIndex() == PAGE_INDEX["Commands"]
    assert window.page_title.text() == "Commands"

    actions["Run diagnostics"].trigger()
    assert window.stack.currentIndex() == PAGE_INDEX["Diagnostics"]

    actions["Security Center"].trigger()
    assert window.stack.currentIndex() == PAGE_INDEX["Security"]

    actions["Settings"].trigger()
    assert window.stack.currentIndex() == PAGE_INDEX["Settings"]

    actions["New connection"].trigger()
    assert window.stack.currentIndex() == PAGE_INDEX["Workspace"]

    actions["Assistant beside terminal"].trigger()
    assert window.stack.currentIndex() == PAGE_INDEX["Workspace"]
    assert window.workspace.tabs.currentWidget() is window.workspace.terminal_page
    assert window.workspace.assistant_panel.isVisible()
    window.close()


def test_theme_selection_applies_live_and_persists(app, services):
    from nexus_remote.ui.theme import apply_theme

    window = _window(services)
    index = window.settings.theme_combo.findData("daylight")
    assert index >= 0

    window.settings.theme_combo.setCurrentIndex(index)

    assert services.database.get_setting("ui.theme") == "daylight"
    assert "#f4f7f2" in app.styleSheet()
    assert window.windowIcon().isNull() is False

    apply_theme(app, "matrix")
    window.close()
