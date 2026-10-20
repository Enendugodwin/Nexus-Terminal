"""Application themes.

Colours are declared once as tokens so pages never hard-code literals. The
default is the original hacker-green workspace; three alternatives are bundled
and can be selected in Settings. ``TOKENS`` is deliberately a single mutable
mapping: modules import the mapping once and always read the active values, so
switching a theme at runtime needs no widget rebuild.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    tokens: Mapping[str, str]
    ansi: Mapping[str, str]


_MATRIX = {
    "bg": "#040704",
    "bg_alt": "#07100a",
    "panel": "#0a160d",
    "panel_alt": "#0e1f13",
    "border": "#1b3a22",
    "border_strong": "#2e5a36",
    "text": "#d9ffe0",
    "text_muted": "#86c994",
    "text_faint": "#4f7f59",
    "accent": "#00ff41",
    "accent_hover": "#62ff88",
    "accent_soft": "#0f3a18",
    "accent_text": "#001a08",
    "ok": "#3dff8b",
    "warn": "#ffc857",
    "danger": "#ff5c5c",
    "term_bg": "#040704",
    "term_fg": "#d9ffe0",
    "term_selection": "#0f3a18",
    "mono": "'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono', monospace",
    "ui": "'Segoe UI Variable', 'Segoe UI', 'Inter', sans-serif",
}

_MIDNIGHT = {
    "bg": "#05070f",
    "bg_alt": "#090e1a",
    "panel": "#0b1220",
    "panel_alt": "#101a2c",
    "border": "#1d2f4a",
    "border_strong": "#2d4a70",
    "text": "#dbe7ff",
    "text_muted": "#8fa8c9",
    "text_faint": "#56708f",
    "accent": "#4cc9ff",
    "accent_hover": "#8bdcff",
    "accent_soft": "#0d2b3d",
    "accent_text": "#001521",
    "ok": "#42e3a0",
    "warn": "#ffc857",
    "danger": "#ff6b6b",
    "term_bg": "#04070e",
    "term_fg": "#dbe7ff",
    "term_selection": "#123047",
    "mono": "'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono', monospace",
    "ui": "'Segoe UI Variable', 'Segoe UI', 'Inter', sans-serif",
}

_EMBER = {
    "bg": "#0b0603",
    "bg_alt": "#140c06",
    "panel": "#190f07",
    "panel_alt": "#21150b",
    "border": "#3a2413",
    "border_strong": "#5e3a1d",
    "text": "#ffe9d6",
    "text_muted": "#c9a184",
    "text_faint": "#806048",
    "accent": "#ff8a3d",
    "accent_hover": "#ffb07a",
    "accent_soft": "#3a1a08",
    "accent_text": "#1a0a02",
    "ok": "#7ddc7d",
    "warn": "#ffd166",
    "danger": "#ff6259",
    "term_bg": "#0b0603",
    "term_fg": "#ffe9d6",
    "term_selection": "#3a1a08",
    "mono": "'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono', monospace",
    "ui": "'Segoe UI Variable', 'Segoe UI', 'Inter', sans-serif",
}

_DAYLIGHT = {
    "bg": "#f4f7f2",
    "bg_alt": "#e7efe8",
    "panel": "#ffffff",
    "panel_alt": "#eef4ef",
    "border": "#c9d8cc",
    "border_strong": "#9fbbaa",
    "text": "#14251a",
    "text_muted": "#4b6353",
    "text_faint": "#7a9182",
    "accent": "#0b7a3b",
    "accent_hover": "#0f9c4b",
    "accent_soft": "#d8efe0",
    "accent_text": "#ffffff",
    "ok": "#0b7a3b",
    "warn": "#9a6100",
    "danger": "#b3261e",
    "term_bg": "#fbfdf9",
    "term_fg": "#16231a",
    "term_selection": "#cfe6d2",
    "mono": "'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono', monospace",
    "ui": "'Segoe UI Variable', 'Segoe UI', 'Inter', sans-serif",
}

_DARK_ANSI = {
    "black": "#0a140c",
    "red": "#ff5f56",
    "green": "#00ff41",
    "brown": "#ffc857",
    "yellow": "#ffd873",
    "blue": "#57a8ff",
    "magenta": "#d17ae8",
    "cyan": "#5ce1c4",
    "white": "#d9ffe0",
    "brightblack": "#3f6b47",
    "brightred": "#ff8a82",
    "brightgreen": "#6dff8f",
    "brightbrown": "#ffd98a",
    "brightyellow": "#ffe599",
    "brightblue": "#8fc3ff",
    "brightmagenta": "#e3a6f5",
    "brightcyan": "#8ff0dc",
    "brightwhite": "#f2fff5",
}

_LIGHT_ANSI = {
    "black": "#1d2b21",
    "red": "#b3261e",
    "green": "#0a7d35",
    "brown": "#8a5a00",
    "yellow": "#7a5c00",
    "blue": "#1a5fb4",
    "magenta": "#8e24aa",
    "cyan": "#00707a",
    "white": "#16231a",
    "brightblack": "#6b7d70",
    "brightred": "#d43a31",
    "brightgreen": "#0a8f3c",
    "brightbrown": "#946e00",
    "brightyellow": "#946e00",
    "brightblue": "#2b74cc",
    "brightmagenta": "#a13cc0",
    "brightcyan": "#008a96",
    "brightwhite": "#0b130e",
}

THEMES: dict[str, Theme] = {
    "matrix": Theme("matrix", "Matrix (green)", _MATRIX, _DARK_ANSI),
    "midnight": Theme("midnight", "Midnight (blue)", _MIDNIGHT, _DARK_ANSI),
    "ember": Theme("ember", "Ember (amber)", _EMBER, _DARK_ANSI),
    "daylight": Theme("daylight", "Daylight (light)", _DAYLIGHT, _LIGHT_ANSI),
}

DEFAULT_THEME = "matrix"

#: The active token mapping. Mutated in place by :func:`set_theme`.
TOKENS: dict[str, str] = dict(THEMES[DEFAULT_THEME].tokens)
_ACTIVE = DEFAULT_THEME


def theme_choices() -> list[tuple[str, str]]:
    """``(id, label)`` pairs in display order."""
    return [(theme.name, theme.label) for theme in THEMES.values()]


def current_theme() -> str:
    return _ACTIVE


def set_theme(name: str | None) -> str:
    """Activate a theme by name, falling back to the default."""
    key = (name or "").strip().lower()
    if key not in THEMES:
        key = DEFAULT_THEME
    global _ACTIVE
    _ACTIVE = key
    TOKENS.clear()
    TOKENS.update(THEMES[key].tokens)
    return key


def ansi_colours() -> Mapping[str, str]:
    return THEMES[_ACTIVE].ansi


def stylesheet() -> str:
    t = TOKENS
    return f"""
    * {{
        font-family: {t['ui']};
        font-size: 10.5pt;
    }}
    QWidget {{
        background-color: {t['bg']};
        color: {t['text']};
    }}
    QMainWindow::separator {{
        background: {t['border']};
        width: 1px;
        height: 1px;
    }}
    #Sidebar {{
        background-color: {t['bg_alt']};
        border-right: 1px solid {t['border']};
    }}
    #BrandTitle {{
        font-size: 13pt;
        font-weight: 600;
        color: {t['accent']};
    }}
    #SidebarSubtitle {{
        color: {t['text_faint']};
        font-size: 8.5pt;
        padding: 0 16px 12px 16px;
    }}
    #NavList {{
        background: transparent;
        border: none;
        outline: none;
    }}
    #NavList::item {{
        padding: 9px 14px;
        margin: 2px 8px;
        border-radius: 7px;
        color: {t['text_muted']};
    }}
    #NavList::item:selected {{
        background-color: {t['accent_soft']};
        color: {t['accent']};
    }}
    #NavList::item:hover:!selected {{
        background-color: {t['panel_alt']};
    }}
    #TopBar {{
        background-color: {t['bg_alt']};
        border-bottom: 1px solid {t['border']};
    }}
    #PageTitle {{
        font-size: 15pt;
        font-weight: 600;
        color: {t['accent']};
    }}
    #PageSubtitle {{
        color: {t['text_muted']};
        font-size: 9.5pt;
    }}
    #Card {{
        background-color: {t['panel']};
        border: 1px solid {t['border']};
        border-radius: 10px;
    }}
    #CardTitle {{
        font-size: 10.5pt;
        font-weight: 600;
    }}
    #MetricValue {{
        font-size: 20pt;
        font-weight: 600;
        color: {t['accent']};
    }}
    #MetricLabel {{
        color: {t['text_muted']};
        font-size: 9pt;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    #Muted {{
        color: {t['text_muted']};
    }}
    #Faint {{
        color: {t['text_faint']};
        font-size: 9pt;
    }}
    #SysCard {{
        background-color: {t['panel']};
        border: 1px solid {t['border']};
        border-radius: 8px;
    }}
    #SysTitle {{
        color: {t['text_muted']};
        font-size: 8.5pt;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    #SysValue {{
        font-size: 12pt;
        font-weight: 600;
        color: {t['accent']};
    }}
    #Mono, QPlainTextEdit#Mono, QTextEdit#Mono, QLineEdit#Mono {{
        font-family: {t['mono']};
        font-size: 9.5pt;
    }}
    QPushButton {{
        background-color: {t['panel_alt']};
        border: 1px solid {t['border_strong']};
        border-radius: 7px;
        padding: 6px 13px;
        color: {t['text']};
    }}
    QPushButton:hover {{
        border-color: {t['accent']};
    }}
    QPushButton:pressed {{
        background-color: {t['accent_soft']};
    }}
    QPushButton:disabled {{
        color: {t['text_faint']};
        border-color: {t['border']};
        background-color: {t['bg_alt']};
    }}
    QPushButton#Primary {{
        background-color: {t['accent']};
        border-color: {t['accent']};
        color: {t['accent_text']};
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{
        background-color: {t['accent_hover']};
    }}
    QPushButton#Danger {{
        border-color: {t['danger']};
        color: {t['danger']};
    }}
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit, QListWidget, QTableWidget, QTreeWidget {{
        background-color: {t['panel_alt']};
        border: 1px solid {t['border']};
        border-radius: 7px;
        padding: 5px 7px;
        selection-background-color: {t['accent_soft']};
        selection-color: {t['text']};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
        border-color: {t['accent']};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {t['panel_alt']};
        border: 1px solid {t['border_strong']};
        selection-background-color: {t['accent_soft']};
    }}
    QHeaderView::section {{
        background-color: {t['bg_alt']};
        color: {t['text_muted']};
        border: none;
        border-bottom: 1px solid {t['border']};
        padding: 6px 8px;
        font-size: 9.5pt;
    }}
    QTableWidget, QTreeWidget {{
        gridline-color: {t['border']};
        alternate-background-color: {t['panel']};
    }}
    QTabWidget::pane {{
        border: 1px solid {t['border']};
        border-radius: 8px;
        background-color: {t['panel']};
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {t['text_muted']};
        padding: 7px 14px;
        margin-right: 4px;
        border-radius: 7px;
    }}
    QTabBar::tab:selected {{
        background-color: {t['panel_alt']};
        color: {t['accent']};
    }}
    QGroupBox {{
        border: 1px solid {t['border']};
        border-radius: 8px;
        margin-top: 10px;
        padding-top: 12px;
        color: {t['text_muted']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }}
    QStatusBar {{
        background-color: {t['bg_alt']};
        border-top: 1px solid {t['border']};
        color: {t['text_muted']};
    }}
    QProgressBar {{
        background-color: {t['panel_alt']};
        border: 1px solid {t['border']};
        border-radius: 6px;
        text-align: center;
        color: {t['text_muted']};
        height: 16px;
    }}
    QProgressBar::chunk {{
        background-color: {t['accent']};
        border-radius: 5px;
    }}
    QScrollBar:vertical, QScrollBar:horizontal {{
        background: transparent;
        width: 10px;
        height: 10px;
    }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        background: {t['border_strong']};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        height: 0;
        width: 0;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid {t['border_strong']};
        border-radius: 4px;
        background: {t['panel_alt']};
    }}
    QCheckBox::indicator:checked {{
        background: {t['accent']};
        border-color: {t['accent']};
    }}
    QToolTip {{
        background-color: {t['panel_alt']};
        color: {t['text']};
        border: 1px solid {t['border_strong']};
        padding: 4px;
    }}
    """


def _palette() -> Any:
    from PySide6.QtGui import QColor, QPalette

    t = TOKENS
    colours = {
        "Window": t["bg"],
        "WindowText": t["text"],
        "Base": t["panel_alt"],
        "AlternateBase": t["panel"],
        "Text": t["text"],
        "PlaceholderText": t["text_faint"],
        "Button": t["panel_alt"],
        "ButtonText": t["text"],
        "Highlight": t["accent_soft"],
        "HighlightedText": t["text"],
        "ToolTipBase": t["panel_alt"],
        "ToolTipText": t["text"],
        "Link": t["accent"],
        "Disabled:WindowText": t["text_faint"],
        "Disabled:Text": t["text_faint"],
        "Disabled:ButtonText": t["text_faint"],
        "Disabled:Highlight": t["border"],
        "Disabled:HighlightedText": t["text_faint"],
    }
    palette = QPalette()
    for role, value in colours.items():
        disabled = role.startswith("Disabled:")
        enum_name = role.split(":", 1)[1] if disabled else role
        enum = getattr(QPalette.ColorRole, enum_name, None)
        if enum is None:
            continue
        group = QPalette.ColorGroup.Disabled if disabled else QPalette.ColorGroup.Active
        palette.setColor(group, enum, QColor(value))
        if not disabled:
            palette.setColor(QPalette.ColorGroup.Inactive, enum, QColor(value))
    return palette


def apply_theme(application: Any, name: str | None = None) -> str:
    """Apply (and remember) a theme on a QApplication. Returns the theme id."""
    selected = set_theme(name or _ACTIVE)
    try:
        application.setStyle("Fusion")
    except Exception:  # pragma: no cover - style plugin availability
        pass
    application.setPalette(_palette())
    application.setStyleSheet(stylesheet())
    return selected
