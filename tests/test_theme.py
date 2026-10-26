"""Theme tokens, live switching and persisted application icons."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from nexus_remote.resources import resource_path
from nexus_remote.ui import theme


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_bundled_themes_are_complete_and_distinct():
    required = {
        "bg",
        "bg_alt",
        "panel",
        "panel_alt",
        "border",
        "border_strong",
        "text",
        "text_muted",
        "text_faint",
        "accent",
        "accent_hover",
        "accent_soft",
        "accent_text",
        "ok",
        "warn",
        "danger",
        "term_bg",
        "term_fg",
        "term_selection",
        "mono",
        "ui",
    }
    choices = theme.theme_choices()

    assert len(choices) >= 4
    assert [key for key, _label in choices][:4] == ["matrix", "midnight", "ember", "daylight"]
    for key, label in choices:
        assert label
        assert required <= set(theme.THEMES[key].tokens)
        assert theme.THEMES[key].ansi


def test_set_theme_mutates_the_shared_token_mapping():
    mapping = theme.TOKENS
    try:
        theme.set_theme("daylight")

        assert theme.current_theme() == "daylight"
        assert theme.TOKENS["bg"].startswith("#f")
        assert mapping is theme.TOKENS  # imports stay valid across a switch
        assert theme.ansi_colours()["green"].startswith("#0a")
    finally:
        theme.set_theme("matrix")


def test_unknown_theme_falls_back_to_default():
    assert theme.set_theme("does-not-exist") == "matrix"
    assert theme.current_theme() == "matrix"


def test_apply_theme_changes_the_application(app):
    selected = theme.apply_theme(app, "midnight")

    assert selected == "midnight"
    assert theme.TOKENS["accent"] == "#4cc9ff"
    assert "#4cc9ff" in app.styleSheet()
    assert app.palette().window().color().name().lower() == theme.TOKENS["bg"]

    theme.apply_theme(app, "matrix")


def test_a_disabled_primary_button_looks_disabled(app):
    """An id selector must not keep a disabled Start/Delete button lit."""

    theme.apply_theme(app, "matrix")
    sheet = app.styleSheet()

    assert "QPushButton#Primary:disabled" in sheet
    assert "QPushButton#Danger:disabled" in sheet

    from PySide6.QtWidgets import QPushButton

    button = QPushButton("Start")
    button.setObjectName("Primary")
    button.show()
    button.setEnabled(True)
    app.processEvents()
    image = button.grab().toImage()
    enabled = image.pixelColor(image.width() // 2, image.height() // 2).name()
    button.setEnabled(False)
    app.processEvents()
    image = button.grab().toImage()
    disabled = image.pixelColor(image.width() // 2, image.height() // 2).name()
    button.deleteLater()

    assert enabled != disabled, "a disabled Primary button rendered like an enabled one"


def test_icon_resources_exist_and_load(app):
    from PySide6.QtGui import QIcon

    icon_path = resource_path("nexus.ico")
    image_path = resource_path("nexus.png")

    assert icon_path.is_file()
    assert image_path.is_file()
    assert not QIcon(str(icon_path)).isNull()
    assert not QIcon(str(icon_path)).pixmap(32, 32).isNull()
