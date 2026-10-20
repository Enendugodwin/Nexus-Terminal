"""The local PowerShell section and its pseudo-terminal channel."""

from __future__ import annotations

import os
import sys
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
    channel = PtyChannel.spawn(
        option.executable,
        rows=24,
        columns=100,
        arguments=("-NoProfile",),
    )
    try:
        channel.write("Start-Sleep -Seconds 30\r")
        deadline = time.monotonic() + 10
        seen = b""
        while time.monotonic() < deadline:
            chunk = channel.read(timeout=0.2)
            if chunk:
                seen += chunk
            if b"Start-Sleep" in seen:
                break
        # Ctrl+C is ETX on a terminal. If it is not delivered as a real
        # interrupt, the marker below waits behind the 30-second sleep.
        channel.write(b"\x03")
        time.sleep(0.2)
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
