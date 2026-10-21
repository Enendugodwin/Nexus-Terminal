"""Headless tests for terminal emulation and key encoding."""

from __future__ import annotations

import pyte

from nexus_remote.ui.terminal import Cell, TerminalBuffer, encode_key


def test_plain_text_and_cursor_position():
    buffer = TerminalBuffer(40, 10)
    buffer.feed("hello\r\nworld")
    assert buffer.line_text(0) == "hello"
    assert buffer.line_text(1) == "world"
    assert buffer.cursor == (1, 5)


def test_utf8_bytes_are_decoded():
    buffer = TerminalBuffer(20, 5)
    buffer.feed("héllo ✓".encode())
    assert buffer.line_text(0) == "héllo ✓"


def test_scrollback_keeps_previous_screen_lines():
    buffer = TerminalBuffer(20, 3, history=50)
    for index in range(10):
        buffer.feed(f"line{index}\r\n")
    assert buffer.history_size >= 8
    text = buffer.text()
    assert "line0" in text
    assert "line9" in text
    assert buffer.line_text(buffer.total_rows - 1) == ""


def test_resizing_updates_columns_and_rows():
    buffer = TerminalBuffer(80, 24)
    buffer.feed("abc")
    buffer.resize(120, 40)
    assert (buffer.columns, buffer.lines) == (120, 40)
    assert buffer.line_text(0) == "abc"


def test_escape_sequences_move_the_cursor_and_clear():
    buffer = TerminalBuffer(20, 5)
    buffer.feed("abc\r\ndef")
    buffer.feed("\x1b[2A\r\x1b[2C")  # up two rows, then two columns from home
    assert buffer.cursor == (0, 2)
    buffer.feed("\x1b[K")  # erase to end of line
    assert buffer.line_text(0) == "ab"


def test_colour_attributes_are_exposed_per_cell():
    buffer = TerminalBuffer(20, 3)
    buffer.feed("\x1b[1;31mred\x1b[0m plain")
    first = buffer.row(0)
    assert first[0].char == "r"
    assert first[0].fg == "red"
    assert first[0].bold is True
    assert first[3].fg == "default"


def test_256_colour_values_become_hex():
    buffer = TerminalBuffer(20, 3)
    buffer.feed("\x1b[38;5;196mX")
    assert buffer.row(0)[0].fg.lower() == "ff0000"


def test_title_and_modes_come_from_escape_sequences():
    buffer = TerminalBuffer(20, 5)
    buffer.feed("\x1b]0;prod-shell\x07")
    assert buffer.title == "prod-shell"
    buffer.feed("\x1b[?2004h")
    assert buffer.bracketed_paste is True
    buffer.feed("\x1b[?1h")
    assert buffer.application_cursor is True
    buffer.feed("\x1b[?25l")
    assert buffer.cursor_visible is False


def test_selection_text_spans_rows():
    buffer = TerminalBuffer(20, 5)
    buffer.feed("alpha\r\nbravo\r\ncharlie")
    assert buffer.selection_text((0, 2), (0, 4)) == "pha"
    assert buffer.selection_text((0, 3), (1, 2)) == "ha\nbra"
    assert buffer.selection_text((1, 2), (0, 3)) == "ha\nbra"


def test_cursor_key_encoding_plain_and_application_mode():
    assert encode_key("up") == b"\x1b[A"
    assert encode_key("up", application_cursor=True) == b"\x1bOA"
    assert encode_key("home") == b"\x1b[H"
    assert encode_key("end", application_cursor=True) == b"\x1bOF"


def test_special_keys_and_function_keys():
    assert encode_key("enter") == b"\r"
    assert encode_key("backspace") == b"\x7f"
    assert encode_key("tab") == b"\t"
    assert encode_key("tab", shift=True) == b"\x1b[Z"
    assert encode_key("delete") == b"\x1b[3~"
    assert encode_key("pageup") == b"\x1b[5~"
    assert encode_key("f1") == b"\x1bOP"
    assert encode_key("f5") == b"\x1b[15~"
    assert encode_key("f12") == b"\x1b[24~"


def test_control_and_alt_characters():
    assert encode_key("", "c", ctrl=True) == b"\x03"
    assert encode_key("", "C", ctrl=True) == b"\x03"
    assert encode_key("", "[", ctrl=True) == b"\x1b"
    assert encode_key("", "x", alt=True) == b"\x1bx"
    assert encode_key("", "é") == "é".encode()


def test_unknown_key_without_text_sends_nothing():
    assert encode_key("") == b""
    assert encode_key("scrolllock") == b""


def test_cell_defaults_are_safe():
    cell = Cell()
    assert cell.char == " "
    assert cell.fg == "default"


def test_pyte_version_supports_the_features_we_rely_on():
    # HistoryScreen, bracketed-paste passthrough and ByteStream are the
    # compatibility surface; guard against an accidental upgrade breaking them.
    assert hasattr(pyte, "HistoryScreen")
    assert hasattr(pyte, "ByteStream")
