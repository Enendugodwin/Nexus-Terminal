"""Local PowerShell console running on this Windows computer."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.assistant.context import terminal_snapshot
from nexus_remote.host_shell import (
    PtyChannel,
    available_shells,
    default_shell,
)
from nexus_remote.services import AppServices
from nexus_remote.ui.assistant_panel import AssistantPanel
from nexus_remote.ui.tasks import run_async
from nexus_remote.ui.terminal import TerminalView

WELCOME = (
    "Nexus Remote - local PowerShell\r\n"
    "This console runs on this computer with your Windows account. It is not a remote "
    "session and the remote command policy does not filter it.\r\n"
    "Press Start PowerShell to open the shell.\r\n"
)


class PowerShellPage(QWidget):
    """An explicit, operator-driven local shell.

    The shell never starts on launch. The operator chooses the edition and
    working directory, then presses Start; closing the app terminates it.
    """

    status_message = Signal(str)

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self._channel: PtyChannel | None = None
        self._starting = False
        self._start_cancelled = False
        self._pending_option: object | None = None
        self._pending_cwd: Path = Path.home()

        self.shell_combo = QComboBox()
        self._shells = available_shells()
        for option in self._shells:
            self.shell_combo.addItem(option.label, option.key)
        preferred = self.services.database.get_setting("local_shell.shell", "") or ""
        index = self.shell_combo.findData(preferred)
        if index < 0 and self._shells:
            preferred_default = default_shell()
            if preferred_default is not None:
                index = self.shell_combo.findData(preferred_default.key)
        self.shell_combo.setCurrentIndex(max(0, index))

        self.cwd_edit = QLineEdit(
            self.services.database.get_setting("local_shell.cwd", "") or str(Path.home())
        )
        self.cwd_edit.setPlaceholderText("working directory for the new shell")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)

        self.start_button = QPushButton("Start PowerShell")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self.start_shell)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("Danger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_shell)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_terminal)
        self.diagnose_button = QPushButton("Diagnose last error")
        self.diagnose_button.setToolTip(
            "Copy the console output into the assistant's error tab and diagnose it immediately."
        )
        self.diagnose_button.clicked.connect(self.diagnose_last_error)

        self.status = QLabel("Stopped. Nothing runs until you press Start PowerShell.")
        self.status.setObjectName("Faint")
        self.status.setWordWrap(True)

        self.terminal_view = TerminalView()
        self.terminal_view.closed.connect(self._shell_closed)
        self.terminal_view.bell.connect(lambda: self.status_message.emit("PowerShell bell"))
        self.terminal_view.feed(WELCOME)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Shell"))
        controls.addWidget(self.shell_combo, 1)
        controls.addWidget(QLabel("Working directory"))
        controls.addWidget(self.cwd_edit, 2)
        controls.addWidget(browse)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(clear)
        controls.addWidget(self.diagnose_button)

        note = QLabel(
            "This shell is local: it uses your Windows account and is not governed by the remote "
            "command policy. AI suggestions are never typed or run automatically; the assistant "
            "only inserts text when you press its button, and you still press Enter."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)

        console = QWidget()
        self._console_page = console
        console_layout = QVBoxLayout(console)
        console_layout.setContentsMargins(0, 0, 0, 0)
        console_layout.addLayout(controls)
        console_layout.addWidget(note)
        console_layout.addWidget(self.terminal_view, 1)
        console_layout.addWidget(self.status)

        self.assistant_panel = AssistantPanel(self.services)
        self.assistant_panel.set_environment("powershell")
        self.assistant_panel.set_context("Local PowerShell on this computer")
        self.assistant_panel.send_shell_requested.connect(self._send_to_local_shell)
        self.assistant_panel.insert_text_requested.connect(self._insert_text_in_shell)
        self.assistant_panel.set_context_provider(self._terminal_context)
        self.assistant_panel.status_message.connect(self.status_message)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(console, "Console")
        self.tabs.addTab(self.assistant_panel, "Assistant")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.tabs, 1)

        if not self._shells:
            self.start_button.setEnabled(False)
            self.status.setText(
                "No PowerShell executable was found. Windows PowerShell or PowerShell 7 is required."
            )

    # -- lifecycle ----------------------------------------------------------
    def selected_shell(self):
        key = self.shell_combo.currentData()
        for option in self._shells:
            if option.key == key:
                return option
        return default_shell()

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Working directory", self.cwd_edit.text() or str(Path.home())
        )
        if selected:
            self.cwd_edit.setText(selected)

    def start_shell(self) -> None:
        if self.terminal_view.is_attached() or self._starting:
            return
        option = self.selected_shell()
        if option is None:
            QMessageBox.warning(
                self,
                "PowerShell not found",
                "Install Windows PowerShell or PowerShell 7, then reopen this page.",
            )
            return
        cwd = Path(self.cwd_edit.text().strip() or str(Path.home())).expanduser()
        if not cwd.is_dir():
            QMessageBox.information(
                self,
                "Working directory",
                f"{cwd} is not a folder; the shell will start in {Path.home()}.",
            )
            cwd = Path.home()
            self.cwd_edit.setText(str(cwd))
        columns, rows = self.terminal_view.term_size()
        # The console is started off the GUI thread: a backend that cannot
        # start reports the failure immediately instead of freezing the window
        # until the pseudo-terminal gives up.
        self._starting = True
        self._start_cancelled = False
        self._pending_option = option
        self._pending_cwd = cwd
        self.start_button.setEnabled(False)
        self.status.setText("Starting PowerShell...")
        run_async(
            PtyChannel.spawn,
            option.executable,
            cwd=cwd,
            rows=rows,
            columns=columns,
            label=option.label,
            on_result=self._shell_started,
            on_error=self._shell_failed,
        )

    def _shell_started(self, channel: object) -> None:
        self._starting = False
        if self._start_cancelled or not isinstance(channel, PtyChannel):
            if isinstance(channel, PtyChannel):
                channel.close()
            self._set_stopped("PowerShell stopped.")
            return
        option = self._pending_option
        cwd = self._pending_cwd
        columns, rows = self.terminal_view.term_size()
        label = option.label if option else "PowerShell"
        self._channel = channel
        self.terminal_view.attach(
            channel,
            session_id=f"local-{option.key if option else 'powershell'}",
            label=f"{label} on this computer ({cwd})",
        )
        channel.resize(columns, rows)
        self.shell_combo.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText(f"{label} is running on this computer in {cwd}.")
        self.status_message.emit(f"Local {label} started.")
        self.services.database.set_setting("local_shell.shell", option.key if option else "")
        self.services.database.set_setting("local_shell.cwd", str(cwd))
        self.services.audit.record(
            "system",
            "local_shell.started",
            severity="notice",
            outcome="success",
            detail={"shell": label, "cwd": str(cwd)},
        )

    def _shell_failed(self, message: str, detail: str = "") -> None:
        """Report a failed start at once; the backend is bounded, so this is quick."""
        self._starting = False
        self.shell_combo.setEnabled(bool(self._shells))
        self.start_button.setEnabled(bool(self._shells))
        text = str(message or "PowerShell did not start.")
        self.status.setText(f"PowerShell did not start: {text}")
        QMessageBox.warning(self, "Could not start PowerShell", text)

    def stop_shell(self) -> None:
        if self._starting:
            # The console is still being created; cancel it and let the
            # background start close the process when it arrives.
            self._start_cancelled = True
            self._starting = False
            self._set_stopped("PowerShell stopped.")
            self.status_message.emit("Local PowerShell stopped.")
            return
        if not self.terminal_view.is_attached() and self._channel is None:
            return
        self.terminal_view.detach()
        self._channel = None
        self._set_stopped("PowerShell stopped.")
        self.services.audit.record(
            "system",
            "local_shell.stopped",
            severity="info",
            outcome="success",
        )
        self.status_message.emit("Local PowerShell stopped.")

    # -- assistant ----------------------------------------------------------
    def _send_to_local_shell(self, command: str) -> None:
        """Insert an assistant command into the local console; never press Enter.

        The text is inserted exactly as the operator typed it. The operator
        still reviews it and presses Enter, and nothing is typed automatically.
        """

        command = (command or "").strip()
        if not command:
            return
        if not self.terminal_view.is_attached():
            self.status_message.emit(
                "PowerShell is not running - press Start PowerShell before inserting a command."
            )
            return
        self.terminal_view.paste(command)
        self.status_message.emit(
            "Command inserted into the local console - review it, then press Enter to run it."
        )

    def diagnose_last_error(self) -> None:
        """Send the console output to the assistant and diagnose it straight away."""

        text = self.terminal_view.buffer.text()
        if not text.strip():
            self.status_message.emit("There is no console output to diagnose yet.")
            return
        self.assistant_panel.diagnose_text(text)
        self.tabs.setCurrentWidget(self.assistant_panel)
        self.status_message.emit("The console output was diagnosed in the assistant tab.")

    def _terminal_context(self) -> str:
        """A short description of the local console, used as AI context."""

        if not self.terminal_view.is_attached():
            return ""
        option = self._pending_option or self.selected_shell()
        label = option.label if option else "PowerShell"
        return terminal_snapshot(
            shell=f"{label} (local console on this computer)",
            details=[
                ("Working directory", self.cwd_edit.text().strip()),
                ("Operator account", os.environ.get("USERNAME", "")),
            ],
            output=self.terminal_view.buffer.text(),
        )

    def _insert_text_in_shell(self, text: str) -> None:
        """Insert selected AI text into the console without running it."""

        if not self.terminal_view.is_attached():
            QApplication.clipboard().setText(text)
            self.status_message.emit(
                "PowerShell is not running - the selection was copied to the clipboard."
            )
            return
        if self.terminal_view.insert_text(text):
            self.tabs.setCurrentWidget(self._console_page)
            self.terminal_view.setFocus()
            self.status_message.emit(
                "Selection inserted into the console - review it, then press Enter to run it."
            )
            return
        QApplication.clipboard().setText(text)
        self.status_message.emit(
            "This console cannot take a multi-line paste without running it, so the selection "
            "was copied to the clipboard instead."
        )

    def _shell_closed(self, _session_id: str) -> None:
        self._channel = None
        self._set_stopped("PowerShell exited.")
        self.status_message.emit("Local PowerShell exited.")

    def _set_stopped(self, message: str) -> None:
        self.shell_combo.setEnabled(bool(self._shells))
        self.start_button.setEnabled(bool(self._shells))
        self.stop_button.setEnabled(False)
        self.status.setText(message)

    def clear_terminal(self) -> None:
        self.terminal_view.clear()
        if not self.terminal_view.is_attached():
            self.terminal_view.feed(WELCOME)

    def shutdown(self) -> None:
        """Called when the main window closes; never leaves a child shell behind."""

        if self.terminal_view.is_attached() or self._channel is not None:
            self.terminal_view.detach()
            self._channel = None
