"""Command input with inline (ghost) completion.

The suggestion engine already ranks commands; this widget makes its top hit
visible *while typing* instead of only in a list below the field. The remainder
is painted in the faint token colour, so it is clearly a proposal and never
part of the text: it cannot be submitted, copied or selected until accepted.

Keys: Tab or Right-arrow at the end of the line accepts, Escape dismisses, and
Up/Down are re-emitted as :attr:`navigate` so a companion list can be driven
without taking focus away from the editor.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLineEdit, QWidget

from nexus_remote.ui.theme import TOKENS


class SuggestionLineEdit(QLineEdit):
    """A single-line editor that previews the completion of the typed command."""

    accepted = Signal(str)  # the suggestion that was accepted
    navigate = Signal(int)  # -1 for Up, +1 for Down
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ghost = ""
        self.textChanged.connect(self._on_text_changed)
        self.cursorPositionChanged.connect(self._on_text_changed)

    # -- suggestion state ---------------------------------------------------
    def ghost(self) -> str:
        """The completion currently previewed after the cursor (may be empty)."""
        return self._ghost

    def suggestion_text(self) -> str:
        """The remainder that is actually painted, e.g. ``"tall -y"``."""
        if not self._ghost:
            return ""
        return self._ghost[len(self.text()) :]

    def set_suggestion(self, text: str | None) -> None:
        """Preview ``text`` as the completion of the current line, if it fits.

        A suggestion is only shown when it completes what has been typed (a
        case-insensitive prefix), the cursor is at the end and nothing is
        selected. Anything else would promise text the operator did not type.
        """
        ghost = self._acceptable(text or "")
        if ghost == self._ghost:
            return
        self._ghost = ghost
        self.update()

    def clear_suggestion(self) -> None:
        if self._ghost:
            self._ghost = ""
            self.update()

    def accept_suggestion(self) -> bool:
        """Replace the line with the previewed completion; True if one existed."""
        if not self._ghost:
            return False
        text = self._ghost
        self._ghost = ""
        self.setText(text)
        self.setCursorPosition(len(text))
        self.update()
        self.accepted.emit(text)
        return True

    def _acceptable(self, text: str) -> str:
        typed = self.text()
        if not text or not typed or text == typed:
            return ""
        if self.hasSelectedText():
            return ""
        if self.cursorPosition() != len(typed):
            return ""
        if not text.casefold().startswith(typed.casefold()):
            return ""
        return text

    def _on_text_changed(self, *_args: object) -> None:
        if self._ghost and self._acceptable(self._ghost) != self._ghost:
            self._ghost = ""
            self.update()

    # -- input --------------------------------------------------------------
    def event(self, event) -> bool:
        """Accept on Tab before Qt turns it into focus traversal.

        ``QWidget.event()`` consumes Tab for focus navigation before
        ``keyPressEvent`` is reached, so completion has to be handled here.
        """
        if event.type() == event.Type.KeyPress and event.key() == Qt.Key.Key_Tab and self._ghost:
            self.accept_suggestion()
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        key = event.key()
        if key == Qt.Key.Key_Tab and self._ghost:
            self.accept_suggestion()
            event.accept()
            return
        if (
            key == Qt.Key.Key_Right
            and self._ghost
            and self.cursorPosition() == len(self.text())
        ):
            self.accept_suggestion()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._ghost:
            self.clear_suggestion()
            self.dismissed.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            # A single-line editor has no use for these; a companion completion
            # list does. The event is consumed so focus stays in the editor.
            self.navigate.emit(-1 if key == Qt.Key.Key_Up else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        remainder = self.suggestion_text()
        if not remainder:
            return
        rect = self.contentsRect()
        cursor = self.cursorRect()
        available = QRect(
            cursor.left() + 1,
            rect.top(),
            rect.right() - cursor.left() - 1,
            rect.height(),
        )
        if available.width() < self.fontMetrics().horizontalAdvance("m"):
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(QColor(TOKENS["text_faint"]))
        painter.drawText(
            available,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.fontMetrics().elidedText(
                remainder, Qt.TextElideMode.ElideRight, available.width()
            ),
        )
