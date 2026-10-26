"""Widget-level terminal tests (offscreen Qt, no real SSH connection)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from nexus_remote.ui.terminal import TerminalView


class FakeChannel:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks = list(chunks or [])
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.closed = False
        self.close_calls = 0

    def read(self, timeout: float = 0.2, max_bytes: int = 65536) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""

    def write(self, data: bytes | str) -> int:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        self.writes.append(payload)
        return len(payload)

    def resize(self, width: int, height: int) -> None:
        self.resizes.append((width, height))

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def _key(view: TerminalView, key: Qt.Key, text: str = "", modifiers=Qt.KeyboardModifier.NoModifier):
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text)
    view.keyPressEvent(event)


def test_attach_feeds_output_and_reports_title(app):
    view = TerminalView()
    view.resize(900, 500)
    channel = FakeChannel([b"\x1b]0;prod\x07hello\r\n"])
    view.attach(channel, session_id="s1", label="prod")
    assert view.is_attached()
    assert view.session_id == "s1"
    view._drain()
    assert "hello" in view.buffer.text()
    assert view.buffer.title == "prod"
    view.close()
    view.deleteLater()


def test_resize_propagates_a_pty_resize(app):
    view = TerminalView()
    view.resize(640, 320)
    channel = FakeChannel()
    view.attach(channel)
    view.resize(1000, 700)
    view._apply_geometry()
    assert channel.resizes
    assert channel.resizes[-1] == view.term_size()
    view.deleteLater()


def test_keystrokes_are_encoded_and_sent(app):
    view = TerminalView()
    channel = FakeChannel()
    view.attach(channel)
    for char in "ls":
        _key(view, Qt.Key.Key_L if char == "l" else Qt.Key.Key_S, char)
    _key(view, Qt.Key.Key_Return, "\r")
    assert b"".join(channel.writes) == b"ls\r"
    channel.writes.clear()
    _key(view, Qt.Key.Key_Up, "")
    assert channel.writes == [b"\x1b[A"]
    view.deleteLater()


def test_ctrl_c_sends_interrupt_without_a_selection(app):
    view = TerminalView()
    channel = FakeChannel()
    view.attach(channel)
    _key(view, Qt.Key.Key_C, "\x03", Qt.KeyboardModifier.ControlModifier)
    assert channel.writes[-1] == b"\x03"
    view.deleteLater()


def test_selection_copy_uses_clipboard(app):
    view = TerminalView()
    view.attach(FakeChannel())
    view.feed("hello world")
    view._anchor, view._selection = (0, 0), (0, 4)
    assert view.copy_selection() == "hello"
    assert QApplication.clipboard().text() == "hello"
    view.deleteLater()


def test_bracketed_paste_is_wrapped(app):
    view = TerminalView()
    channel = FakeChannel()
    view.attach(channel)
    view.feed("\x1b[?2004h")
    view.paste("one\ntwo")
    assert channel.writes[-1] == b"\x1b[200~one\rtwo\x1b[201~"
    view.deleteLater()


def test_detach_closes_only_the_shell_channel(app):
    view = TerminalView()
    channel = FakeChannel()
    view.attach(channel)
    view.detach()
    assert channel.close_calls == 1
    assert not view.is_attached()
    view.paste("ignored")
    assert channel.writes == []
    view.deleteLater()


def test_closed_channel_emits_once_and_marks_detached(app):
    view = TerminalView()
    channel = FakeChannel()
    seen: list[str] = []
    view.closed.connect(seen.append)
    view.attach(channel, session_id="s9")
    channel.closed = True
    view._drain()
    assert seen == ["s9"]
    assert not view.is_attached()
    view.deleteLater()


def test_paint_renders_without_errors(app):
    view = TerminalView()
    view.resize(600, 300)
    view.attach(FakeChannel())
    view.feed("\x1b[1;32mok\x1b[0m normal\r\n")
    image = view.grab()
    assert not image.isNull()
    view.deleteLater()


def _wheel(view: TerminalView, delta: int) -> None:
    event = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    view.wheelEvent(event)


def test_the_wheel_scrolls_up_and_back_down(app):
    """Wheel up must go back through history; wheel down must return to the bottom."""

    view = TerminalView()
    view.resize(500, 240)
    view.attach(FakeChannel())
    for line in range(200):
        view.feed(f"line {line}\r\n")
    assert view._scroll == 0

    _wheel(view, 120)  # one notch up
    assert view._scroll == 3

    _wheel(view, 120)
    assert view._scroll == 6

    _wheel(view, -120)  # one notch down
    assert view._scroll == 3, "wheel-down must move towards the bottom again"

    _wheel(view, -120)
    assert view._scroll == 0

    _wheel(view, -120)  # cannot scroll past the newest line
    assert view._scroll == 0
    view.deleteLater()


def _attached_view():
    view = TerminalView()
    channel = FakeChannel()
    view.attach(channel, session_id="test", label="test")
    return view, channel


def test_inserting_one_line_never_presses_enter(app):
    view, channel = _attached_view()
    assert view.insert_text("Get-ChildItem C:\\Temp") is True
    assert channel.writes == [b"Get-ChildItem C:\\Temp"]


def test_a_multiline_block_is_refused_without_bracketed_paste(app):
    view, channel = _attached_view()
    assert view.buffer.bracketed_paste is False

    assert view.insert_text("cd /srv\nls -la") is False
    assert channel.writes == [], "a block must not be typed where it could run line by line"


def test_a_multiline_block_is_inserted_as_one_paste_when_supported(app):
    view, channel = _attached_view()
    view.feed("\x1b[?2004h")  # the shell enabled bracketed paste
    assert view.buffer.bracketed_paste is True

    assert view.insert_text("cd /srv\r\nls -la\n") is True
    payload = b"".join(channel.writes)
    assert payload.startswith(b"\x1b[200~")
    assert payload.endswith(b"\x1b[201~")
    assert b"cd /srv\nls -la" in payload
    assert b"\r" not in payload


class FailingChannel(FakeChannel):
    """A channel whose socket dies between polls."""

    def read(self, timeout: float = 0.2, max_bytes: int = 65536) -> bytes:
        raise OSError("An existing connection was forcibly closed by the remote host")


def test_a_local_shell_ending_says_so_instead_of_connection_closed(app):
    view = TerminalView()
    channel = FakeChannel()
    view.attach(channel, session_id="local-powershell", label="PowerShell")
    channel.closed = True

    view._drain()

    text = view.buffer.text()
    assert "[shell exited]" in text
    assert "connection closed" not in text
    view.deleteLater()


def test_a_read_error_closes_the_terminal_instead_of_raising(app):
    view = TerminalView()
    channel = FailingChannel()
    seen: list[str] = []
    view.closed.connect(seen.append)
    view.attach(channel, session_id="s-broken")

    view._drain()  # must not propagate out of the QTimer slot

    assert seen == ["s-broken"]
    assert not view.is_attached()
    assert "read failed" in view.buffer.text()
    view.deleteLater()
