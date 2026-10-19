"""Interactive terminal emulation for SSH shells.

Paramiko transports bytes; it does not interpret escape sequences. This module
does that in two layers:

* :class:`TerminalBuffer` wraps ``pyte`` (a VT100/xterm emulator) and is pure
  Python, so it can be tested without a display.
* :class:`TerminalView` paints the buffer, sends keystrokes and propagates PTY
  resizes to the remote shell.

The emulator is deliberately self-contained: remote output is untrusted data
that is only ever rendered, never executed or evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from nexus_remote.ui.theme import TOKENS

try:  # pyte is a declared dependency; fail with a clear message otherwise.
    import pyte
except ImportError as exc:  # pragma: no cover - packaging guard
    raise ImportError(
        "interactive terminal support requires pyte; run: pip install pyte"
    ) from exc

_BLANK = pyte.screens.Char(" ")

#: pyte reports 256-colour values as bare hex strings and named ANSI colours as
#: words. Map both to something Qt can paint.
_ANSI_COLOURS = {
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
    "brightyellow": "#ffe9a8",
    "brightblue": "#8cc7ff",
    "brightmagenta": "#e9b0ff",
    "brightcyan": "#9af2e0",
    "brightwhite": "#ffffff",
}

_SPECIAL_KEYS = {
    "insert": b"\x1b[2~",
    "delete": b"\x1b[3~",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
}

_CURSOR_KEYS = {
    "up": "A",
    "down": "B",
    "right": "C",
    "left": "D",
    "home": "H",
    "end": "F",
}

#: pyte shifts DEC private modes left by five bits (``mode << 5``).
_BRACKETED_PASTE = 2004 << 5
#: DECCKM (application cursor keys).
_APPLICATION_CURSOR = 1 << 5
#: DECTCEM (cursor visible).
_CURSOR_VISIBLE = 25 << 5


def encode_key(
    name: str,
    text: str = "",
    *,
    ctrl: bool = False,
    alt: bool = False,
    shift: bool = False,
    application_cursor: bool = False,
) -> bytes:
    """Translate a logical key press into the bytes a shell expects.

    ``name`` is a lowercase key name (``"enter"``, ``"up"``, ``"f5"``) or empty
    for a printable character. Keeping this pure makes key handling testable.
    """
    if name == "enter":
        return b"\r"
    if name == "backspace":
        return b"\x7f" if not ctrl else b"\x08"
    if name == "tab":
        return b"\x1b[Z" if shift else b"\t"
    if name == "escape":
        return b"\x1b"
    if name in _CURSOR_KEYS:
        letter = _CURSOR_KEYS[name]
        if name in {"home", "end"}:
            # xterm sends these as ESC [ H / ESC [ F; application mode uses SS3.
            return (f"\x1bO{letter}" if application_cursor else f"\x1b[{letter}").encode()
        return (f"\x1bO{letter}" if application_cursor else f"\x1b[{letter}").encode()
    if name in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[name]

    if ctrl and len(text) == 1:
        char = text.upper()
        if "A" <= char <= "Z":
            return bytes([ord(char) - 64])
        controls = {
            " ": b"\x00",
            "[": b"\x1b",
            "\\": b"\x1c",
            "]": b"\x1d",
            "^": b"\x1e",
            "_": b"\x1f",
            "?": b"\x7f",
            "/": b"\x1f",
        }
        if char in controls:
            return controls[char]
    if ctrl and name == "space":
        return b"\x00"
    if not text:
        return b""
    payload = text.encode("utf-8")
    if alt:
        payload = b"\x1b" + payload
    return payload


@dataclass(frozen=True)
class Cell:
    """A display-ready cell, detached from pyte's mutable screen."""

    char: str = " "
    fg: str = "default"
    bg: str = "default"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False


def _to_cell(raw: object) -> Cell:
    if raw is None:
        return Cell()
    return Cell(
        char=getattr(raw, "data", " ") or " ",
        fg=getattr(raw, "fg", "default") or "default",
        bg=getattr(raw, "bg", "default") or "default",
        bold=bool(getattr(raw, "bold", False)),
        italic=bool(getattr(raw, "italics", False)),
        underline=bool(getattr(raw, "underscore", False)),
        reverse=bool(getattr(raw, "reverse", False)),
    )


class TerminalBuffer:
    """A scrollback-aware VT screen driven by remote bytes."""

    def __init__(self, columns: int = 80, lines: int = 24, history: int = 2000) -> None:
        self.columns = max(1, int(columns))
        self.lines = max(1, int(lines))
        self.screen = pyte.HistoryScreen(self.columns, self.lines, history=max(0, int(history)))
        self.stream = pyte.ByteStream(self.screen)

    # -- input --------------------------------------------------------------
    def feed(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        if data:
            self.stream.feed(data)

    def resize(self, columns: int, lines: int) -> None:
        columns, lines = max(1, int(columns)), max(1, int(lines))
        if (columns, lines) == (self.columns, self.lines):
            return
        self.columns, self.lines = columns, lines
        self.screen.resize(lines, columns)

    # -- views --------------------------------------------------------------
    @property
    def history_size(self) -> int:
        return len(self.screen.history.top)

    @property
    def total_rows(self) -> int:
        return self.history_size + self.lines

    @property
    def title(self) -> str:
        return getattr(self.screen, "title", "") or ""

    @property
    def cursor_visible(self) -> bool:
        return _CURSOR_VISIBLE in self.screen.mode

    @property
    def application_cursor(self) -> bool:
        return _APPLICATION_CURSOR in self.screen.mode

    @property
    def bracketed_paste(self) -> bool:
        return _BRACKETED_PASTE in self.screen.mode

    @property
    def cursor(self) -> tuple[int, int]:
        """Absolute (row, column), counting scrollback before the screen."""
        return self.history_size + int(self.screen.cursor.y), int(self.screen.cursor.x)

    def row(self, index: int) -> list[Cell]:
        if index < 0 or index >= self.total_rows:
            return []
        history = self.screen.history.top
        offset = self.history_size
        raw_row = history[index] if index < offset else self.screen.buffer[index - offset]
        return [_to_cell(raw_row.get(column)) for column in range(self.columns)]

    def rows(self, start: int, count: int) -> list[list[Cell]]:
        start = max(0, min(start, self.total_rows))
        return [self.row(index) for index in range(start, min(start + count, self.total_rows))]

    def line_text(self, index: int, *, strip: bool = True) -> str:
        text = "".join(cell.char for cell in self.row(index))
        return text.rstrip() if strip else text

    def text(self) -> str:
        return "\n".join(self.line_text(index) for index in range(self.total_rows))

    def selection_text(self, anchor: tuple[int, int], cursor: tuple[int, int]) -> str:
        """Return the text between two (row, column) positions, inclusive."""
        (start_row, start_col), (end_row, end_col) = sorted([anchor, cursor])
        if start_row == end_row:
            line = self.line_text(start_row, strip=False)
            return line[start_col : end_col + 1].rstrip()
        pieces = [self.line_text(start_row, strip=False)[start_col:].rstrip()]
        for row in range(start_row + 1, end_row):
            pieces.append(self.line_text(row))
        pieces.append(self.line_text(end_row, strip=False)[: end_col + 1].rstrip())
        return "\n".join(pieces)


class TerminalView(QWidget):
    """Interactive terminal widget backed by :class:`TerminalBuffer`."""

    closed = Signal(str)  # session id
    title_changed = Signal(str)
    bell = Signal()

    PADDING = 8.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.buffer = TerminalBuffer()
        self.session_id = ""
        self.channel = None
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._drain)

        self._font = QFont("Cascadia Mono")
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setPointSizeF(10.0)
        self._font.setFixedPitch(True)
        self._bold = QFont(self._font)
        self._bold.setBold(True)
        self._italic = QFont(self._font)
        self._italic.setItalic(True)
        self._bold_italic = QFont(self._bold)
        self._bold_italic.setItalic(True)

        self._scroll = 0  # lines above the bottom
        self._anchor: tuple[int, int] | None = None
        self._selection: tuple[int, int] | None = None
        self._selecting = False
        self._last_columns = 0
        self._last_rows = 0

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(360, 180)
        self.setAutoFillBackground(True)
        self._apply_geometry()

    # -- lifecycle ----------------------------------------------------------
    def attach(self, channel, *, session_id: str = "", label: str = "") -> None:
        """Attach an open shell channel (anything with read/write/resize)."""
        self.detach()
        self.channel = channel
        self.session_id = session_id
        self.buffer = TerminalBuffer(self._last_columns or 80, self._last_rows or 24)
        self._scroll = 0
        self._clear_selection()
        if label:
            self.buffer.feed(f"[{label}]\r\n")
        self._apply_geometry()
        self._timer.start()
        self.setFocus()
        self.update()

    def detach(self) -> None:
        self._timer.stop()
        if self.channel is not None:
            try:
                self.channel.close()
            except Exception:  # pragma: no cover - defensive
                pass
        self.channel = None
        self.session_id = ""
        self._scroll = 0
        self.update()

    def is_attached(self) -> bool:
        return self.channel is not None

    # -- terminal I/O -------------------------------------------------------
    def feed(self, data: bytes | str) -> None:
        before = self.buffer.total_rows
        self.buffer.feed(data)
        if self._scroll > 0:
            # Keep the operator's position stable while output arrives.
            self._scroll += max(0, self.buffer.total_rows - before)
        self._trim_scroll()
        self.update()

    def send(self, data: bytes | str) -> None:
        if self.channel is None:
            return
        if self._scroll:
            self._scroll = 0
        try:
            self.channel.write(data)
        except Exception as exc:  # pragma: no cover - channel race
            self.feed(f"\r\n[write failed: {exc}]\r\n")

    def paste(self, text: str | None = None) -> None:
        payload = QApplication.clipboard().text() if text is None else text
        if not payload:
            return
        payload = payload.replace("\r\n", "\n").replace("\n", "\r")
        if self.buffer.bracketed_paste:
            payload = f"\x1b[200~{payload}\x1b[201~"
        self.send(payload)

    def _drain(self) -> None:
        if self.channel is None:
            return
        received = b""
        for _ in range(8):  # bounded so a flooding remote cannot starve the UI
            chunk = self.channel.read(timeout=0.0, max_bytes=65536)
            if not chunk:
                break
            received += chunk
        if received:
            self.feed(received)
            title = self.buffer.title
            if title:
                self.title_changed.emit(title)
            if b"\x07" in received:
                self.bell.emit()
        elif getattr(self.channel, "closed", False):
            self._timer.stop()
            self.feed("\r\n[connection closed]\r\n")
            session_id, self.session_id = self.session_id, ""
            self.channel = None
            self.closed.emit(session_id)

    # -- geometry -----------------------------------------------------------
    def term_size(self) -> tuple[int, int]:
        return self._last_columns or 80, self._last_rows or 24

    def _apply_geometry(self) -> None:
        metrics = QFontMetricsF(self._font)
        advance = max(1.0, metrics.horizontalAdvance("M"))
        line_height = max(1.0, metrics.height())
        columns = max(20, int((self.width() - 2 * self.PADDING) // advance))
        rows = max(4, int((self.height() - 2 * self.PADDING) // line_height))
        if (columns, rows) == (self._last_columns, self._last_rows):
            return
        self._last_columns, self._last_rows = columns, rows
        self.buffer.resize(columns, rows)
        if self.channel is not None:
            try:
                self.channel.resize(columns, rows)
            except Exception:  # pragma: no cover - server dependent
                pass
        self._trim_scroll()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_geometry()

    def _trim_scroll(self) -> None:
        maximum = max(0, self.buffer.total_rows - self._last_rows)
        self._scroll = max(0, min(self._scroll, maximum))

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        metrics = QFontMetricsF(self._font)
        advance = max(1.0, metrics.horizontalAdvance("M"))
        line_height = max(1.0, metrics.height())
        ascent = metrics.ascent()
        background = QColor(TOKENS["bg"])
        painter.fillRect(self.rect(), background)

        rows = self._last_rows or 24
        columns = self._last_columns or 80
        total = self.buffer.total_rows
        bottom = max(0, total - self._scroll)
        start = max(0, bottom - rows)
        selection = self._normalised_selection()

        for line_index, cells in enumerate(self.buffer.rows(start, rows)):
            row = start + line_index
            top = self.PADDING + line_index * line_height
            if not cells:
                cells = [Cell() for _ in range(columns)]
            run_start = 0
            run_style = self._cell_style(cells[0], row, 0, selection) if cells else None
            for column in range(1, columns + 1):
                cell = cells[column] if column < len(cells) else Cell()
                style = self._cell_style(cell, row, column, selection) if column < columns else None
                if style != run_style and run_style is not None:
                    self._draw_run(
                        painter,
                        run_style,
                        "".join(cell.char for cell in cells[run_start:column]),
                        self.PADDING + run_start * advance,
                        top,
                        (column - run_start) * advance,
                        line_height,
                        ascent,
                    )
                    run_start, run_style = column, style
        self._paint_cursor(painter, start, rows, advance, line_height, ascent)
        self._paint_scroll_hint(painter)

    @staticmethod
    def _colour(value: str, default: str) -> QColor:
        if not value or value == "default":
            return QColor(default)
        if value.startswith("#"):
            return QColor(value)
        if len(value) in (3, 6) and all(char in "0123456789abcdefABCDEF" for char in value):
            return QColor(f"#{value}")
        return QColor(_ANSI_COLOURS.get(value.lower(), default))

    def _cell_style(
        self,
        cell: Cell,
        row: int,
        column: int,
        selection: tuple[tuple[int, int], tuple[int, int]] | None,
    ) -> tuple[str, str, bool, bool, bool, bool]:
        fg = cell.fg
        bg = cell.bg
        if cell.reverse:
            fg, bg = bg, fg
        selected = False
        if selection is not None:
            (start_row, start_col), (end_row, end_col) = selection
            if start_row == end_row:
                selected = row == start_row and start_col <= column <= end_col
            elif start_row < row < end_row:
                selected = True
            elif row == start_row:
                selected = column >= start_col
            elif row == end_row:
                selected = column <= end_col
        if selected:
            return (TOKENS["text"], TOKENS["accent_soft"], cell.bold, cell.italic, cell.underline, True)
        return (fg, bg, cell.bold, cell.italic, cell.underline, False)

    def _draw_run(
        self,
        painter: QPainter,
        style: tuple[str, str, bool, bool, bool, bool],
        text: str,
        x: float,
        y: float,
        width: float,
        height: float,
        ascent: float,
    ) -> None:
        fg, bg, bold, italic, underline, selected = style
        if not text:
            return
        if bg != "default" or selected:
            painter.fillRect(
                QRectF(x, y, width, height),
                self._colour(bg, TOKENS["accent_soft"] if selected else TOKENS["bg"]),
            )
        if not text.strip() and bg == "default" and not selected:
            return
        font = (
            self._bold_italic
            if bold and italic
            else self._bold
            if bold
            else self._italic
            if italic
            else self._font
        )
        painter.setFont(font)
        colour = self._colour(fg, TOKENS["text"])
        painter.setPen(colour)
        painter.drawText(QPoint(int(x), int(y + ascent)), text)
        if underline:
            painter.drawLine(
                int(x), int(y + height - 1), int(x + width), int(y + height - 1)
            )

    def _paint_cursor(
        self,
        painter: QPainter,
        start: int,
        rows: int,
        advance: float,
        line_height: float,
        ascent: float,
    ) -> None:
        if not self.hasFocus() or not self.buffer.cursor_visible:
            return
        cursor_row, cursor_column = self.buffer.cursor
        if not start <= cursor_row < start + rows:
            return
        column = min(cursor_column, (self._last_columns or 80) - 1)
        x = self.PADDING + column * advance
        y = self.PADDING + (cursor_row - start) * line_height
        painter.fillRect(QRectF(x, y, advance, line_height), QColor(TOKENS["accent"]))
        cells = self.buffer.row(cursor_row)
        char = cells[column].char if column < len(cells) else " "
        painter.setFont(self._font)
        painter.setPen(QColor(TOKENS["bg"]))
        painter.drawText(QPoint(int(x), int(y + ascent)), char if char.strip() else " ")

    def _paint_scroll_hint(self, painter: QPainter) -> None:
        if not self._scroll:
            return
        painter.setFont(self._font)
        painter.setPen(QColor(TOKENS["warn"]))
        painter.drawText(
            self.rect().adjusted(0, 0, -12, -6),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
            f"scrollback -{self._scroll} (End to return)",
        )

    # -- input --------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if self.channel is None:
            return
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        key = event.key()

        if ctrl and shift and key == Qt.Key.Key_C:
            self.copy_selection()
            return
        if ctrl and shift and key == Qt.Key.Key_V:
            self.paste()
            return
        if shift and key == Qt.Key.Key_Insert:
            self.paste()
            return
        if ctrl and key == Qt.Key.Key_C and self._has_selection():
            self.copy_selection()
            return

        name = _key_name(key)
        if ctrl and shift and key == Qt.Key.Key_A:
            self.select_all()
            return
        if not name and not event.text():
            return
        self._scroll = 0
        self.send(
            encode_key(
                name,
                event.text(),
                ctrl=ctrl,
                alt=alt,
                shift=shift,
                application_cursor=self.buffer.application_cursor,
            )
        )
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API
        delta = event.angleDelta().y()
        if delta == 0:
            return
        step = max(1, delta // 120) * 3
        self._scroll = min(
            max(0, self.buffer.total_rows - (self._last_rows or 24)),
            max(0, self._scroll + step),
        )
        self.update()

    def _cell_at(self, position: QPoint) -> tuple[int, int]:
        metrics = QFontMetricsF(self._font)
        advance = max(1.0, metrics.horizontalAdvance("M"))
        line_height = max(1.0, metrics.height())
        column = max(0, int((position.x() - self.PADDING) // advance))
        line = max(0, int((position.y() - self.PADDING) // line_height))
        column = min(column, (self._last_columns or 80) - 1)
        start = max(0, self.buffer.total_rows - self._scroll - (self._last_rows or 24))
        row = min(start + line, max(0, self.buffer.total_rows - 1))
        return row, column

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            point = self._cell_at(event.position().toPoint())
            self._anchor = point
            self._selection = None
            self._selecting = True
            self.update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.paste()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if not self._selecting or self._anchor is None:
            return
        self._selection = self._cell_at(event.position().toPoint())
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._selecting = False
            if self._selection == self._anchor:
                self._selection = None
            self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        row, column = self._cell_at(event.position().toPoint())
        cells = self.buffer.row(row)
        if not cells:
            return
        start = column
        while start > 0 and not cells[start - 1].char.isspace():
            start -= 1
        end = column
        while end + 1 < len(cells) and not cells[end + 1].char.isspace():
            end += 1
        self._anchor, self._selection = (row, start), (row, end)
        self.update()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API
        menu = QMenu(self)
        copy = menu.addAction("Copy")
        copy.setEnabled(self._has_selection())
        copy.triggered.connect(self.copy_selection)
        paste = menu.addAction("Paste")
        paste.setEnabled(self.channel is not None)
        paste.triggered.connect(self.paste)
        select_all = menu.addAction("Select all")
        select_all.triggered.connect(self.select_all)
        menu.addSeparator()
        clear = menu.addAction("Clear scrollback")
        clear.triggered.connect(self.clear)
        menu.exec(event.globalPos())

    # -- clipboard ----------------------------------------------------------
    def _normalised_selection(self):
        if self._anchor is None or self._selection is None:
            return None
        return tuple(sorted([self._anchor, self._selection]))

    def _has_selection(self) -> bool:
        return self._normalised_selection() is not None

    def select_all(self) -> None:
        total = self.buffer.total_rows
        if total:
            self._anchor = (0, 0)
            self._selection = (total - 1, (self._last_columns or 80) - 1)
            self.update()

    def copy_selection(self) -> str:
        selection = self._normalised_selection()
        if selection is None:
            return ""
        text = self.buffer.selection_text(*selection)
        QApplication.clipboard().setText(text)
        return text

    def clear(self) -> None:
        self.buffer = TerminalBuffer(self._last_columns or 80, self._last_rows or 24)
        self._scroll = 0
        self._clear_selection()
        self.update()

    def _clear_selection(self) -> None:
        self._anchor = None
        self._selection = None
        self._selecting = False


def _key_name(key: int) -> str:
    names = {
        Qt.Key.Key_Return: "enter",
        Qt.Key.Key_Enter: "enter",
        Qt.Key.Key_Backspace: "backspace",
        Qt.Key.Key_Tab: "tab",
        Qt.Key.Key_Escape: "escape",
        Qt.Key.Key_Up: "up",
        Qt.Key.Key_Down: "down",
        Qt.Key.Key_Left: "left",
        Qt.Key.Key_Right: "right",
        Qt.Key.Key_Home: "home",
        Qt.Key.Key_End: "end",
        Qt.Key.Key_Insert: "insert",
        Qt.Key.Key_Delete: "delete",
        Qt.Key.Key_PageUp: "pageup",
        Qt.Key.Key_PageDown: "pagedown",
        Qt.Key.Key_Space: "space",
    }
    for index in range(1, 13):
        names[getattr(Qt.Key, f"Key_F{index}")] = f"f{index}"
    return names.get(key, "")
