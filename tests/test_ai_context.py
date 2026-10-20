"""Bounded terminal snapshots that travel with an AI question."""

from __future__ import annotations

from nexus_remote.assistant.context import last_lines, summarise, terminal_snapshot


def test_only_the_last_lines_are_kept():
    text = "\n".join(f"line {index}" for index in range(500))
    tail = last_lines(text, limit=5)
    assert tail.splitlines() == ["line 495", "line 496", "line 497", "line 498", "line 499"]


def test_blank_lines_are_dropped_and_the_size_is_capped():
    text = "a\n\n\n" + ("x" * 5000) + "\n\n"
    tail = last_lines(text, limit=40, max_chars=120)
    assert len(tail) <= 120
    assert tail == tail.rstrip()


def test_a_snapshot_says_what_it_knows_and_skips_empties():
    snapshot = terminal_snapshot(
        shell="Windows PowerShell 5.1 (local console)",
        details=[("Working directory", "C:\\Temp"), ("Environment", "")],
        output="PS C:\\Temp> Get-ChildItem\nmode  name\n-a--- file.txt",
    )
    lines = snapshot.splitlines()
    assert lines[0] == "Shell: Windows PowerShell 5.1 (local console)"
    assert "Working directory: C:\\Temp" in lines
    assert not any(line.startswith("Environment:") for line in lines)
    assert "Recent terminal output:" in lines
    assert lines[-1] == "-a--- file.txt"
    assert "1 line" in summarise(snapshot) or "line" in summarise(snapshot)


def test_an_empty_snapshot_summarises_to_nothing():
    assert summarise("") == ""
    assert summarise("   \n  ") == ""
