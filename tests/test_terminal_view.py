"""Widget-level terminal tests (offscreen Qt, no real SSH connection)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
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
