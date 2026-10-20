"""Application theme: a hacker-green workspace on black.

Colours are declared once as tokens so pages never hard-code literals.
"""

from __future__ import annotations

from typing import Any

TOKENS: dict[str, str] = {
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
    "ok": "#3dff8b",
    "warn": "#ffc857",
    "danger": "#ff5c5c",
    "mono": "'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono', monospace",
    "ui": "'Segoe UI Variable', 'Segoe UI', 'Inter', sans-serif",
}

SEVERITY_COLOURS = {
    "info": TOKENS["text_muted"],
    "notice": TOKENS["accent"],
    "warning": TOKENS["warn"],
    "critical": TOKENS["danger"],
    "success": TOKENS["ok"],
    "failure": TOKENS["danger"],
    "denied": TOKENS["warn"],
}


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
    #SidebarTitle {{
        font-size: 13pt;
        font-weight: 600;
        padding: 14px 16px 6px 16px;
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
        color: #001a08;
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

PALETTE: dict[str, Any] = {
    "Window": TOKENS["bg"],
    "WindowText": TOKENS["text"],
    "Base": TOKENS["panel_alt"],
    "AlternateBase": TOKENS["panel"],
    "Text": TOKENS["text"],
    "Button": TOKENS["panel_alt"],
    "ButtonText": TOKENS["text"],
    "Highlight": TOKENS["accent_soft"],
    "HighlightedText": TOKENS["text"],
    "ToolTipBase": TOKENS["panel_alt"],
    "ToolTipText": TOKENS["text"],
}


def apply_theme(application: Any) -> None:
    """Apply the dark palette and stylesheet to a QApplication."""
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    for role, value in PALETTE.items():
        enum = getattr(QPalette.ColorRole, role, None)
        if enum is not None:
            palette.setColor(enum, QColor(value))
    application.setPalette(palette)
    application.setStyleSheet(stylesheet())
    try:
        application.setStyle("Fusion")
    except Exception:  # pragma: no cover - style plugin availability
        pass
