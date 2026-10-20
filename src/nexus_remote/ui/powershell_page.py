"""Local PowerShell console running on this Windows computer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.host_shell import (
    HostShellUnavailable,
    PtyChannel,
    available_shells,
    default_shell,
)
from nexus_remote.services import AppServices
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

        note = QLabel(
            "This shell is local: it uses your Windows account and is not governed by the remote "
            "command policy. AI suggestions are never typed or run automatically; the assistant "
            "only inserts text when you press its button, and you still press Enter."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(controls)
        layout.addWidget(note)
        layout.addWidget(self.terminal_view, 1)
        layout.addWidget(self.status)

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
        if self.terminal_view.is_attached():
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
        try:
            channel = PtyChannel.spawn(
                option.executable,
                cwd=cwd,
                rows=rows,
                columns=columns,
                label=option.label,
            )
        except HostShellUnavailable as exc:
            QMessageBox.warning(self, "Could not start PowerShell", str(exc))
            return
        self._channel = channel
        self.terminal_view.attach(
            channel,
            session_id=f"local-{option.key}",
            label=f"{option.label} on this computer ({cwd})",
        )
        channel.resize(columns, rows)
        self.shell_combo.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText(f"{option.label} is running on this computer in {cwd}.")
        self.status_message.emit(f"Local {option.label} started.")
        self.services.database.set_setting("local_shell.shell", option.key)
        self.services.database.set_setting("local_shell.cwd", str(cwd))
        self.services.audit.record(
            "system",
            "local_shell.started",
            severity="notice",
            outcome="success",
            detail={"shell": option.label, "cwd": str(cwd)},
        )

    def stop_shell(self) -> None:
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
