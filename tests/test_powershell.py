"""The local PowerShell section and its pseudo-terminal channel."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from nexus_remote import host_shell
from nexus_remote.host_shell import PtyChannel, available_shells, default_shell
from nexus_remote.ui.powershell_page import PowerShellPage


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_a_powershell_edition_is_discoverable_on_windows():
    if sys.platform != "win32":
        pytest.skip("the local console is Windows-only")
    options = available_shells()
    assert options
    assert any(option.executable.lower().endswith("powershell.exe") for option in options)
    assert default_shell() is not None


def test_the_page_never_starts_a_shell_by_itself(app, services):
    page = PowerShellPage(services)
    assert page.terminal_view.is_attached() is False
    assert page.stop_button.isEnabled() is False
    assert "Nothing runs" in page.status.text()
    assert page.start_button.isEnabled() is bool(page.shell_combo.count())
    page.shutdown()


def test_the_page_uses_the_saved_shell_and_directory(app, services):
    option = default_shell()
    if option is None:
        pytest.skip("no PowerShell installed")
    services.database.set_setting("local_shell.shell", option.key)
    services.database.set_setting("local_shell.cwd", str(services.paths.root))

    page = PowerShellPage(services)

    assert page.shell_combo.currentData() == option.key
    assert page.cwd_edit.text() == str(services.paths.root)
    page.shutdown()


@pytest.mark.skipif(sys.platform != "win32", reason="the local console is Windows-only")
def test_the_pty_channel_runs_a_real_powershell_command():
    try:
        import winpty  # noqa: F401
    except ImportError:  # pragma: no cover - optional dependency
        pytest.skip("pywinpty is not installed")
    option = default_shell()
    if option is None:
        pytest.skip("no PowerShell installed")

    marker = f"NEXUS_PTY_{uuid.uuid4().hex}"
    channel = PtyChannel.spawn(
        option.executable,
        rows=24,
        columns=100,
        arguments=("-NoProfile",),
    )
    try:
        channel.write(f"Write-Output {marker}\r")
        deadline = time.monotonic() + 20
        received = b""
        while time.monotonic() < deadline:
            chunk = channel.read(timeout=0.2)
            if chunk:
                received += chunk
            if marker.encode() in received:
                break
            if channel.closed:
                break
        assert marker.encode() in received, received[-2000:].decode("utf-8", "replace")
    finally:
        channel.close()


@pytest.mark.skipif(sys.platform != "win32", reason="the local console is Windows-only")
def test_control_c_interrupts_a_running_powershell_command():
    try:
        import winpty  # noqa: F401
    except ImportError:  # pragma: no cover - optional dependency
        pytest.skip("pywinpty is not installed")
    option = default_shell()
    if option is None:
        pytest.skip("no PowerShell installed")

    marker = f"NEXUS_CTRLC_{uuid.uuid4().hex}"
    ready = f"NEXUS_READY_{uuid.uuid4().hex}"
    channel = PtyChannel.spawn(
        option.executable,
        rows=24,
        columns=100,
        arguments=("-NoProfile",),
    )
    try:
        # Wait for a real prompt before typing: echoed keystrokes appear before
        # PowerShell is ready to execute them, which made this test racy on a
        # cold start.
        channel.write(f"Write-Output {ready}\r")
        deadline = time.monotonic() + 20
        seen = b""
        while time.monotonic() < deadline:
            chunk = channel.read(timeout=0.2)
            if chunk:
                seen += chunk
            if ready.encode() in seen:
                break
        assert ready.encode() in seen, seen[-2000:].decode("utf-8", "replace")

        channel.write("Start-Sleep -Seconds 30\r")
        time.sleep(1.0)  # let the command start before interrupting it
        # Ctrl+C is ETX on a terminal. If it is not delivered as a real
        # interrupt, the marker below waits behind the 30-second sleep.
        channel.write(b"\x03")
        time.sleep(0.3)
        channel.write(f"Write-Output {marker}\r")
        deadline = time.monotonic() + 15
        received = b""
        while time.monotonic() < deadline:
            chunk = channel.read(timeout=0.2)
            if chunk:
                received += chunk
            if marker.encode() in received:
                break
            if channel.closed:
                break
        assert marker.encode() in received, received[-2000:].decode("utf-8", "replace")
    finally:
        channel.close()


def test_pty_spawn_reports_a_useful_error(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("the local console is Windows-only")
    monkeypatch.setattr(host_shell, "sys", type("FakeSys", (), {"platform": "win32"}))
    # A nonexistent executable must not escape as a raw OSError.
    with pytest.raises(host_shell.HostShellUnavailable):
        PtyChannel.spawn("Z:\\definitely-not-a-shell.exe", cwd=".")


def test_pty_spawn_fails_quickly_instead_of_hanging(monkeypatch):
    """A bad path must report its error at once, not after a minute of waiting."""

    if sys.platform != "win32":
        pytest.skip("the local console is Windows-only")
    monkeypatch.setattr(host_shell, "sys", type("FakeSys", (), {"platform": "win32"}))
    started = time.monotonic()
    with pytest.raises(host_shell.HostShellUnavailable):
        PtyChannel.spawn("Z:\\definitely-not-a-shell.exe", cwd=".")
    assert time.monotonic() - started < 10


# -- the assistant tab ------------------------------------------------------
class FakeChannel:
    """A stand-in for a live console that records what was written."""

    def __init__(self) -> None:
        self.written: list[str] = []
        self._closed = False

    def read(self, timeout: float = 0.0, max_bytes: int = 65536) -> bytes:
        return b""

    def write(self, data: bytes | str) -> None:
        self.written.append(data if isinstance(data, str) else data.decode("utf-8", "replace"))

    def resize(self, columns: int, rows: int) -> None:
        return None

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


def test_the_page_carries_an_assistant_tab_for_powershell(app, services):
    page = PowerShellPage(services)
    titles = [page.tabs.tabText(index) for index in range(page.tabs.count())]
    assert "Console" in titles
    assert "Assistant" in titles
    assert page.assistant_panel.environment_combo.currentData() == "powershell"
    assert "Local PowerShell" in page.assistant_panel.context_label.text()
    page.shutdown()


def test_an_assistant_command_is_inserted_but_never_executed(app, services):
    page = PowerShellPage(services)
    channel = FakeChannel()
    page.terminal_view.attach(channel, session_id="local-test", label="test")
    page.assistant_panel.send_shell_requested.emit("Get-Date")
    app.processEvents()

    assert channel.written, "the command was not inserted into the console"
    inserted = "".join(channel.written)
    assert "Get-Date" in inserted
    assert "\r" not in inserted and "\n" not in inserted, "Enter must never be sent"
    page.shutdown()


def test_the_console_error_is_diagnosed_immediately(app, services):
    page = PowerShellPage(services)
    page.terminal_view.feed(
        "git : The term 'git' is not recognized as an internal or external command\r\n"
    )
    page.diagnose_last_error()
    app.processEvents()

    assert page.tabs.currentWidget() is page.assistant_panel
    diagnosis = page.assistant_panel.diagnosis_view.toPlainText()
    assert diagnosis.strip(), "the diagnosis was empty"
    assert "not recognized" in diagnosis or "Command not found" in diagnosis
    page.shutdown()


def test_a_failed_start_reports_the_error_without_freezing_the_window(app, services, monkeypatch):
    """The console starts off the GUI thread, so a failure is reported at once."""

    page = PowerShellPage(services)

    def boom(*_args, **_kwargs):
        raise host_shell.HostShellUnavailable("could not start PowerShell: no agent")

    monkeypatch.setattr(host_shell.PtyChannel, "spawn", staticmethod(boom))
    messages: list[str] = []
    page.status_message.connect(messages.append)

    monkeypatch.setattr(
        "nexus_remote.ui.powershell_page.QMessageBox.warning",
        lambda *_args, **_kwargs: None,
    )
    page.start_shell()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and page._starting:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()

    assert page._starting is False
    assert "did not start" in page.status.text()
    assert page.start_button.isEnabled() is True
    page.shutdown()


def test_a_failed_start_is_recorded_in_the_audit_log(app, services, monkeypatch):
    """Successful starts and stops are audited; a failure must be too."""

    page = PowerShellPage(services)

    def boom(*_args, **_kwargs):
        raise host_shell.HostShellUnavailable("could not start PowerShell: no agent")

    monkeypatch.setattr(host_shell.PtyChannel, "spawn", staticmethod(boom))
    monkeypatch.setattr(
        "nexus_remote.ui.powershell_page.QMessageBox.warning",
        lambda *_args, **_kwargs: None,
    )
    page.start_shell()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and page._starting:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()

    events = services.audit.events(limit=10)
    failed = [event for event in events if event.action == "local_shell.failed"]
    assert failed, "the failed start was not audited"
    assert failed[0].outcome == "failure"
    assert "no agent" in failed[0].detail.get("error", "")
    page.shutdown()


def test_a_quoted_working_directory_is_accepted(app, services, monkeypatch, tmp_path):
    """A path pasted from Explorer keeps its quotes; it must still be used."""

    page = PowerShellPage(services)
    seen: dict[str, str] = {}

    def spawn(_executable, *, cwd=None, **_kwargs):
        seen["cwd"] = str(cwd)
        return FakeChannel()

    monkeypatch.setattr(host_shell.PtyChannel, "spawn", staticmethod(spawn))
    page.cwd_edit.setText(f'"{tmp_path}"')

    page.start_shell()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and page._starting:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()

    assert seen["cwd"] == str(tmp_path)
    page.shutdown()


def test_a_multiline_selection_is_inserted_as_one_safe_paste(app, services):
    page = PowerShellPage(services)
    channel = FakeChannel()
    page.terminal_view.attach(channel, session_id="local-test", label="test")
    page.terminal_view.feed("\x1b[?2004h")  # PSReadLine enables bracketed paste

    page._insert_text_in_shell("cd C:\\Temp\nGet-ChildItem")

    payload = "".join(channel.written)
    assert payload.startswith("\x1b[200~") and payload.endswith("\x1b[201~")
    assert "\r" not in payload, "Enter must never be sent"
    page.shutdown()


def test_a_selection_is_copied_when_the_shell_cannot_paste_safely(app, services):
    page = PowerShellPage(services)
    channel = FakeChannel()
    page.terminal_view.attach(channel, session_id="local-test", label="test")
    QApplication.clipboard().setText("")

    page._insert_text_in_shell("cd C:\\Temp\nGet-ChildItem")  # no bracketed paste

    assert channel.written == []
    assert QApplication.clipboard().text() == "cd C:\\Temp\nGet-ChildItem"
    page.shutdown()


def test_the_assistant_gets_the_console_as_context(app, services):
    page = PowerShellPage(services)
    channel = FakeChannel()
    page.terminal_view.attach(channel, session_id="local-test", label="test")
    page.terminal_view.feed("PS C:\\Windows> Get-Service\r\nStatus Name\r\nRunning Spooler\r\n")
    page.cwd_edit.setText("C:\\Windows")

    context = page._terminal_context()

    assert context.startswith("Shell: Windows PowerShell")
    assert "Working directory: C:\\Windows" in context
    assert "Recent terminal output:" in context
    assert "Running Spooler" in context
    page.shutdown()



def test_pty_read_never_drops_bytes_past_the_cap():
    """A capped read must keep the remainder for the next poll."""

    channel = object.__new__(PtyChannel)
    channel._queue = queue.Queue()
    channel._closed = threading.Event()
    channel._backlog = b""
    channel._queue.put(("data", b"0123456789"))

    assert channel.read(timeout=0.0, max_bytes=4) == b"0123"
    assert channel.read(timeout=0.0, max_bytes=100) == b"456789"
    assert channel.read(timeout=0.0, max_bytes=100) == b""


def test_a_pty_backend_error_is_reported_instead_of_silently_closing():
    """The console must say why it died, not just show '[connection closed]'."""

    channel = object.__new__(PtyChannel)
    channel._queue = queue.Queue()
    channel._closed = threading.Event()
    channel._backlog = b""
    channel._queue.put(("data", b"partial"))
    channel._queue.put(("error", "the winpty agent stopped"))
    channel._queue.put(("eof", b""))

    assert channel.read(timeout=0.0, max_bytes=100) == b"partial"
    with pytest.raises(OSError, match="winpty agent stopped"):
        channel.read(timeout=0.0, max_bytes=100)
    # A second poll is quiet rather than raising the same error forever.
    assert channel.read(timeout=0.0, max_bytes=100) == b""


def test_shell_discovery_never_blocks_the_gui_thread(monkeypatch):
    """A PATH entry on a dead network drive must not freeze the page."""

    def slow_which(*_args, **_kwargs):
        time.sleep(0.5)
        return None

    monkeypatch.setattr(host_shell, "LOOKUP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(host_shell.shutil, "which", slow_which)

    started = time.monotonic()
    options = available_shells()
    elapsed = time.monotonic() - started

    assert elapsed < 0.4, f"discovery blocked for {elapsed:.2f}s"
    assert isinstance(options, list)


def test_a_closed_channel_still_reports_buffered_bytes():
    channel = object.__new__(PtyChannel)
    channel._queue = queue.Queue()
    channel._closed = threading.Event()
    channel._backlog = b"tail"
    channel._closed.set()

    assert channel.closed is False, "buffered output must be readable first"
    assert channel.read(timeout=0.0, max_bytes=100) == b"tail"
    assert channel.closed is True


def test_a_late_start_is_closed_instead_of_attaching(app, services):
    """A cancelled spawn that finishes later must not hijack the page."""

    page = PowerShellPage(services)
    page._start_attempt = 7
    late = FakeChannel()

    page._shell_started(late, 6)

    assert late._closed is True
    assert page.terminal_view.is_attached() is False
    assert page.start_button.isEnabled() is True
    page.shutdown()


def test_a_late_failure_from_a_cancelled_start_is_ignored(app, services, monkeypatch):
    page = PowerShellPage(services)
    warnings: list[str] = []
    monkeypatch.setattr(
        "nexus_remote.ui.powershell_page.QMessageBox.warning",
        lambda *_args, **_kwargs: warnings.append("shown"),
    )
    before = page.status.text()
    page._start_attempt = 4

    page._shell_failed("could not start PowerShell", "detail", 3)

    assert warnings == []
    assert page.status.text() == before
    page.shutdown()

