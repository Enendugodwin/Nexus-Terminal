"""Workspace: sessions, SSH console, SFTP/SMB file manager and RDP launch."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.assistant.autocomplete import SuggestionSource, environment_for_os
from nexus_remote.connections.interfaces import ConnectionErrorKind, ProtocolError, SessionState
from nexus_remote.connections.session import Session
from nexus_remote.diagnostics.sysinfo import SystemInfo, collect_system_info
from nexus_remote.services import AppServices
from nexus_remote.ui.assistant_panel import AssistantPanel
from nexus_remote.ui.command_input import SuggestionLineEdit
from nexus_remote.ui.commands_page import CommandDialog
from nexus_remote.ui.system_info import SystemInfoPanel
from nexus_remote.ui.tasks import Worker, connect_worker
from nexus_remote.ui.terminal import TerminalView

SESSION_COLUMNS = ["Device", "Protocol", "Endpoint", "Route", "State", "Detail"]


def _describe_remote_entry(entry: object) -> str:
    """Format an SFTP or SMB entry for the remote list.

    ``RemoteEntry`` carries POSIX permissions, ``SMBEntry`` does not; in that
    case the size is shown instead.
    """
    prefix = "[ ] " if getattr(entry, "is_dir", False) else "    "
    name = str(getattr(entry, "name", ""))
    if getattr(entry, "is_dir", False):
        return f"{prefix}{name}"
    permissions = str(getattr(entry, "permissions", "") or "")
    detail = permissions if permissions.strip("- ") else _human_size(int(getattr(entry, "size", 0) or 0))
    return f"{prefix}{name}   {detail}".rstrip()


def _human_size(size: int) -> str:
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


class WorkspacePage(QWidget):
    """Everything the operator does with a live connection."""

    diagnostic_requested = Signal(str, int, str)  # host, port, protocol
    status_message = Signal(str)

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self._cancel_flags: dict[str, list[bool]] = {}
        self.remote_path = "."
        self.local_path = str(Path.home())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_sessions_tab(), "Sessions")
        self.tabs.addTab(self._build_terminal_tab(), "Terminal")
        self.console_page = self._build_console_tab()
        self.tabs.addTab(self.console_page, "SSH console")
        self.files_page = self._build_files_tab()
        self.tabs.addTab(self.files_page, "Files")
        self.tabs.addTab(self._build_rdp_tab(), "Remote desktop")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addWidget(self.tabs)
        self.refresh_devices()

        # A dead FreeRDP process must not leave a "connected" row behind.
        self._rdp_reaper = QTimer(self)
        self._rdp_reaper.setInterval(2000)
        self._rdp_reaper.timeout.connect(self._reap_rdp_sessions)
        self._rdp_reaper.start()

    def _reap_rdp_sessions(self) -> None:
        for session in self.services.sessions.sessions():
            adapter = session.adapter
            if session.protocol != "rdp" or adapter is None:
                continue
            if session.state is SessionState.CLOSED:
                continue
            error = getattr(adapter, "last_error", "")
            if error:
                session.error = error
                self.services.sessions.force_close(session)
                self._refresh_session_row(session)
                self._refresh_session_choices()
                self.rdp_placeholder.show()
                self.rdp_status.setText(f"RDP failed: {error}.")
                continue
            if getattr(adapter, "exited", False):
                session.error = "the RDP client exited"
                self.services.sessions.force_close(session)
                self._refresh_session_row(session)
                self._refresh_session_choices()
                self.rdp_placeholder.show()
                self.rdp_status.setText(
                    f"The desktop session to {session.endpoint} ended (the client exited)."
                )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        for session in self.services.sessions.sessions():
            adapter = session.adapter
            if session.protocol == "rdp" and adapter is not None:
                resize = getattr(adapter, "resize_embedded", None)
                if callable(resize):
                    resize()

    # ------------------------------------------------------------------ sessions
    def _build_sessions_tab(self) -> QWidget:
        page = QWidget()
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(240)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        self.protocol_combo = QComboBox()
        self.protocol_combo.currentIndexChanged.connect(self._update_route_options)
        self.route_combo = QComboBox()
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("blank = use the stored username")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("blank = use the stored credential")
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("Primary")
        self.connect_button.clicked.connect(self.connect_current)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self.disconnect_selected)
        self.reconnect_button = QPushButton("Reconnect")
        self.reconnect_button.clicked.connect(self.reconnect_selected)
        self.force_button = QPushButton("Force kill")
        self.force_button.setObjectName("Danger")
        self.force_button.setToolTip("Force-terminate the selected session even if it refuses a normal disconnect.")
        self.force_button.clicked.connect(self.force_kill_selected)
        self.kill_all_button = QPushButton("Kill all sessions")
        self.kill_all_button.setObjectName("Danger")
        self.kill_all_button.setToolTip("Force-terminate every open session at once.")
        self.kill_all_button.clicked.connect(self.kill_all_sessions)

        form = QFormLayout()
        form.addRow("Device", self.device_combo)
        form.addRow("Protocol", self.protocol_combo)
        form.addRow("Route", self.route_combo)
        form.addRow("Username", self.username_edit)
        form.addRow("Password / passphrase", self.password_edit)

        buttons = QHBoxLayout()
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.disconnect_button)
        buttons.addWidget(self.reconnect_button)
        buttons.addWidget(self.force_button)
        buttons.addWidget(self.kill_all_button)
        buttons.addStretch(1)

        self.route_hint = QLabel("")
        self.route_hint.setObjectName("Faint")
        self.route_hint.setWordWrap(True)

        self.session_table = QTableWidget(0, len(SESSION_COLUMNS))
        self.session_table.setHorizontalHeaderLabels(SESSION_COLUMNS)
        self.session_table.verticalHeader().setVisible(False)
        self.session_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.session_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.session_table.setAlternatingRowColors(True)

        credentials_note = QLabel(
            "Passwords typed here are used once and never persisted. Stored credentials live in the "
            "Windows Credential Manager and are referenced - not copied - by the device profile."
        )
        credentials_note.setObjectName("Faint")
        credentials_note.setWordWrap(True)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.route_hint)
        layout.addWidget(credentials_note)
        layout.addWidget(self.session_table, 1)
        return page

    def refresh_devices(self) -> None:
        current = self.device_combo.currentData()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for device in self.services.devices.list(sort="name"):
            self.device_combo.addItem(f"{device.name}  ({device.hostname})", device.id)
        index = self.device_combo.findData(current)
        self.device_combo.setCurrentIndex(max(0, index))
        self.device_combo.blockSignals(False)
        self._on_device_changed()

    def _on_device_changed(self) -> None:
        device = self.current_device()
        self.protocol_combo.blockSignals(True)
        self.protocol_combo.clear()
        if device is not None:
            for protocol in device.enabled_protocols():
                self.protocol_combo.addItem(protocol.upper(), protocol)
            self.username_edit.setPlaceholderText(
                "blank = use the stored username" if device.primary_protocol() else "username"
            )
        self.protocol_combo.blockSignals(False)
        self._update_route_options()

    def _update_route_options(self) -> None:
        device = self.current_device()
        self.route_combo.clear()
        if device is None:
            self.route_hint.setText("Add a device to begin.")
            return
        attempts = self.services.routing.plan(device)
        for attempt in attempts:
            label = f"{attempt.method}" + ("" if attempt.allowed else "  (not configured)")
            self.route_combo.addItem(label, attempt.method)
            if not attempt.allowed:
                index = self.route_combo.count() - 1
                self.route_combo.model().item(index).setEnabled(False)
        self.route_hint.setText(
            "Routing: "
            + "; ".join(attempt.describe() for attempt in attempts)
            + "\nOnly routes configured on the device are attempted. A failed ping never triggers an "
            "unconfigured path."
        )

    def current_device(self):
        device_id = self.device_combo.currentData()
        return self.services.devices.get(device_id) if device_id else None

    def open_connection(self, device_id: str, protocol: str) -> None:
        """Called by the device list or dashboard to start a session."""
        index = self.device_combo.findData(device_id)
        if index < 0:
            self.refresh_devices()
            index = self.device_combo.findData(device_id)
        if index >= 0:
            self.device_combo.setCurrentIndex(index)
        protocol_index = self.protocol_combo.findData(protocol)
        if protocol_index >= 0:
            self.protocol_combo.setCurrentIndex(protocol_index)
        self.tabs.setCurrentIndex(0)
        self.connect_current()

    def connect_current(self) -> None:
        device = self.current_device()
        protocol = self.protocol_combo.currentData()
        if device is None or not protocol:
            QMessageBox.information(self, "Nothing to connect", "Select a device and protocol first.")
            return
        route_method = self.route_combo.currentData()
        username = self.username_edit.text().strip() or None
        secret = self.password_edit.text() or None
        try:
            session = self.services.sessions.open(
                device,
                protocol,
                route_method=route_method,
                username=username,
                secret=secret,
            )
        except ProtocolError as exc:
            QMessageBox.warning(self, "Cannot start session", str(exc))
            return
        self.password_edit.clear()
        self._append_session(session)
        self.status_message.emit(session.describe())

        manager = self.services.sessions

        def finished(result: Session) -> None:
            self._refresh_session_row(result)
            self.status_message.emit(result.describe())
            if result.state is SessionState.CONNECTED:
                self._on_connected(result)
            elif result.error_kind == ConnectionErrorKind.HOST_KEY.value:
                self._prompt_host_key(device, result)

        def failed(message: str, detail: str) -> None:
            self.status_message.emit(f"connection failed: {message}")
            self._refresh_session_row(session)
            self._append_console(f"[connect error] {detail}\n")

        worker = Worker(manager.connect, session)
        connect_worker(worker, on_result=finished, on_error=failed)
        QThreadPool.globalInstance().start(worker)

    def _on_connected(self, session: Session) -> None:
        self.services.devices.record_connection(session.device_id, True)
        self._append_console(
            f"[connected] {session.protocol.upper()} {session.endpoint} via {session.route.value}"
            + (f"  banner: {session.server_banner}" if session.server_banner else "")
            + "\n"
        )
        if session.protocol in ("ssh", "serial"):
            self._refresh_session_choices()
            index = self.terminal_session_combo.findData(session.id)
            if index >= 0:
                self.terminal_session_combo.setCurrentIndex(index)
            self._attach_terminal(session)
            self.tabs.setCurrentWidget(self.terminal_page)
        elif session.protocol in ("sftp", "smb"):
            self._refresh_remote_session_combo()
            index = self.file_session_combo.findData(session.id)
            if index >= 0:
                self.file_session_combo.setCurrentIndex(index)
            self.tabs.setCurrentWidget(self.files_page)
        elif session.protocol == "rdp":
            self.tabs.setCurrentIndex(4)
        self._refresh_session_choices()

    def _prompt_host_key(self, device, session: Session) -> None:
        pending = [
            entry
            for entry in self.services.host_keys.pending()
            if entry["host"] == device.hostname or entry["host"].startswith(f"[{device.hostname}]:")
        ]
        if not pending:
            QMessageBox.warning(
                self,
                "Host key not trusted",
                session.error or "The host key could not be verified.",
            )
            return
        entry = pending[0]
        answer = QMessageBox.question(
            self,
            "Verify SSH host key",
            (
                f"{device.hostname} presented a {entry['key_type']} host key:\n\n"
                f"{entry['fingerprint']}\n\n"
                "Compare this fingerprint with the device owner's record before accepting. "
                "Accepting a changed key may mean connecting to an impostor.\n\nTrust this key?"
            ),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.services.host_keys.trust_pending(entry["host"], entry["key_type"])
            self.services.audit.record(
                "host_key",
                "host_key.trusted",
                severity="notice",
                outcome="success",
                device_id=device.id,
                subject=f"{entry['host']} {entry['key_type']}",
                detail={"fingerprint": entry["fingerprint"]},
            )
            self.connect_current()

    def _append_session(self, session: Session) -> None:
        row = self.session_table.rowCount()
        self.session_table.insertRow(row)
        for column, value in enumerate(self._session_values(session)):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, session.id)
            self.session_table.setItem(row, column, item)

    def _refresh_session_row(self, session: Session) -> None:
        for row in range(self.session_table.rowCount()):
            item = self.session_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == session.id:
                for column, value in enumerate(self._session_values(session)):
                    self.session_table.setItem(row, column, QTableWidgetItem(value))
                self.session_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, session.id)
                return
        self._append_session(session)

    def _session_values(self, session: Session) -> list[str]:
        detail = session.error or (session.server_banner or "")
        return [
            session.device_name,
            session.protocol.upper(),
            session.endpoint,
            session.route.value,
            session.state.value,
            detail[:120],
        ]

    def _selected_session(self) -> Session | None:
        rows = self.session_table.selectionModel().selectedRows() if self.session_table.selectionModel() else []
        if not rows:
            return None
        item = self.session_table.item(rows[0].row(), 0)
        return self.services.sessions.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _refresh_session_choices(self) -> None:
        self.console_session_combo.clear()
        terminal_current = self.terminal_session_combo.currentData()
        self.terminal_session_combo.blockSignals(True)
        self.terminal_session_combo.clear()
        for session in self.services.sessions.active_sessions():
            if session.protocol in ("ssh", "sftp") and session.state is SessionState.CONNECTED:
                self.console_session_combo.addItem(
                    f"{session.device_name} ({session.endpoint})", session.id
                )
            if session.protocol in ("ssh", "serial") and session.state is SessionState.CONNECTED:
                self.terminal_session_combo.addItem(
                    f"{session.device_name} ({session.endpoint})", session.id
                )
        if terminal_current:
            index = self.terminal_session_combo.findData(terminal_current)
            if index >= 0:
                self.terminal_session_combo.setCurrentIndex(index)
        self.terminal_session_combo.blockSignals(False)
        if self.terminal_view.is_attached() and self.terminal_view.session_id:
            active = {session.id for session in self.services.sessions.active_sessions()}
            if self.terminal_view.session_id not in active:
                self.terminal_view.detach()
        self._refresh_remote_session_combo()

    def disconnect_selected(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        if self.terminal_view.session_id == session.id:
            self.terminal_view.detach()
        self.services.sessions.disconnect(session)
        self._refresh_session_row(session)
        self.status_message.emit(f"disconnected {session.endpoint}")
        self._refresh_session_choices()

    def reconnect_selected(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        self.services.sessions.disconnect(session)
        self.connect_current()

    def force_kill_selected(self) -> None:
        """Terminate the selected session even if a normal disconnect hangs."""
        session = self._selected_session()
        if session is None:
            QMessageBox.information(self, "No session", "Select a session to force-terminate.")
            return
        self._force_close(session)
        self.status_message.emit(f"force-terminated {session.endpoint}")

    def kill_all_sessions(self) -> None:
        """Kill every session, including ones stuck mid-connect/disconnect."""
        count = self.services.sessions.terminate_all()
        if self.terminal_view.is_attached():
            self.terminal_view.detach()
        self._stop_rdp_embedded()
        for session in self.services.sessions.sessions():
            self._refresh_session_row(session)
        self._refresh_session_choices()
        self.status_message.emit(
            f"terminated {count} session{'s' if count != 1 else ''}" if count else "no active sessions"
        )

    def _force_close(self, session: Session) -> None:
        if self.terminal_view.session_id == session.id:
            self.terminal_view.detach()
        if session.protocol == "rdp":
            self._stop_rdp_embedded()
        else:
            self.services.sessions.force_close(session)
            self._refresh_session_row(session)
        self._refresh_session_choices()

    # ------------------------------------------------------------------ terminal
    def _build_terminal_tab(self) -> QWidget:
        page = QWidget()
        self.terminal_page = page
        self.terminal_session_combo = QComboBox()
        self.terminal_session_combo.setMinimumWidth(240)
        attach = QPushButton("Attach shell")
        attach.setObjectName("Primary")
        attach.clicked.connect(self._attach_selected_terminal_session)
        detach = QPushButton("Detach")
        detach.clicked.connect(self.terminal_view_detach)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.terminal_view.clear())
        self.diagnose_terminal_button = QPushButton("Diagnose last error")
        self.diagnose_terminal_button.setToolTip(
            "Copy the terminal output into the assistant's error tab and diagnose it immediately."
        )
        self.diagnose_terminal_button.clicked.connect(self._diagnose_terminal_output)
        self.assistant_toggle = QPushButton("Assistant")
        self.assistant_toggle.setCheckable(True)
        self.assistant_toggle.setChecked(True)
        self.assistant_toggle.setToolTip(
            "Show or hide the assistant beside the terminal (suggestions, error diagnosis, history)."
        )
        self.assistant_toggle.toggled.connect(self._toggle_assistant)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("SSH session"))
        toolbar.addWidget(self.terminal_session_combo, 1)
        toolbar.addWidget(attach)
        toolbar.addWidget(detach)
        toolbar.addWidget(clear)
        toolbar.addWidget(self.diagnose_terminal_button)
        toolbar.addWidget(self.assistant_toggle)

        self.terminal_status = QLabel("Connect to an SSH device to open an interactive shell.")
        self.terminal_status.setObjectName("Faint")
        self.terminal_status.setWordWrap(True)

        self.terminal_view = TerminalView()
        self.terminal_view.closed.connect(self._on_terminal_closed)
        self.terminal_view.title_changed.connect(lambda title: self.terminal_status.setText(title))
        self.terminal_view.bell.connect(lambda: self.status_message.emit("terminal bell"))

        self.system_info = SystemInfoPanel()
        self.system_info.refresh_requested.connect(self._refresh_system_info)

        terminal_column = QWidget()
        terminal_layout = QVBoxLayout(terminal_column)
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.addWidget(self.terminal_view, 1)
        terminal_layout.addWidget(self.system_info)
        terminal_layout.addWidget(self.terminal_status)

        self.assistant_panel = AssistantPanel(self.services, compact=True)
        self.assistant_panel.send_shell_requested.connect(self._send_to_open_shell)
        self.assistant_panel.status_message.connect(self.status_message)
        self.terminal_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.terminal_splitter.setChildrenCollapsible(False)
        self.terminal_splitter.addWidget(terminal_column)
        self.terminal_splitter.addWidget(self.assistant_panel)
        self.terminal_splitter.setStretchFactor(0, 3)
        self.terminal_splitter.setStretchFactor(1, 1)
        self.terminal_splitter.setSizes([900, 400])

        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        toolbar.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar)
        layout.addWidget(self.terminal_splitter, 1)
        return page

    def _toggle_assistant(self, visible: bool) -> None:
        if visible:
            self.assistant_panel.show()
            width = getattr(self, "_assistant_width", 400)
            total = max(self.terminal_splitter.width(), 640)
            self.terminal_splitter.setSizes([max(total - width, 320), width])
        else:
            self._assistant_width = max(self.assistant_panel.width(), 320)
            self.assistant_panel.hide()

    def _diagnose_terminal_output(self) -> None:
        """Send the terminal output to the assistant and diagnose it at once."""

        text = self.terminal_view.buffer.text()
        if not text.strip():
            self.status_message.emit("There is no terminal output to diagnose yet.")
            return
        self.assistant_panel.diagnose_text(text)
        self.tabs.setCurrentWidget(self.terminal_page)
        self.assistant_toggle.setChecked(True)
        self.assistant_panel.show()
        self.status_message.emit("The terminal output was diagnosed in the assistant panel.")

    def show_terminal_and_assistant(self) -> None:
        """Open the Terminal tab with the assistant visible (used by the menu)."""
        self.tabs.setCurrentWidget(self.terminal_page)
        self.assistant_toggle.setChecked(True)
        self.assistant_panel.show()

    def _send_to_open_shell(self, command: str) -> None:
        """Type an assistant command into the live shell; never press Enter.

        If no shell is attached the command goes to the SSH console instead, so
        the button is never a dead end - and in both cases the operator runs it.
        """
        command = (command or "").strip()
        if not command:
            return
        if self.terminal_view.is_attached():
            self.tabs.setCurrentWidget(self.terminal_page)
            self.terminal_view.paste(command)
            self.terminal_view.setFocus()
            self.status_message.emit(
                "Command inserted into the open shell - review it, then press Enter to run it."
            )
        else:
            self.load_command(command, self._current_environment())
            self.status_message.emit(
                "No shell is attached: the command was loaded into the SSH console instead."
            )

    def terminal_view_detach(self) -> None:
        self.terminal_view.detach()
        self.terminal_status.setText("Terminal detached. The SSH session stays connected.")

    def _attach_selected_terminal_session(self) -> None:
        session_id = self.terminal_session_combo.currentData()
        session = self.services.sessions.get(session_id) if session_id else None
        if session is None:
            QMessageBox.information(self, "No session", "Connect to an SSH device first.")
            return
        self._attach_terminal(session)

    def _attach_terminal(self, session: Session) -> None:
        if session.protocol not in ("ssh", "serial") or session.adapter is None:
            self.terminal_status.setText(
                "Only connected SSH or serial sessions can open an interactive shell."
            )
            return
        if self.terminal_view.session_id == session.id and self.terminal_view.is_attached():
            self.terminal_status.setText(f"Terminal attached to {session.endpoint}.")
            return
        try:
            channel = session.adapter.open_shell(self.terminal_view.term_size())
        except ProtocolError as exc:
            self.terminal_status.setText(f"Could not open a shell: {exc}")
            return
        self.terminal_view.attach(
            channel,
            session_id=session.id,
            label=f"{session.device_name} ({session.endpoint}) via {session.route.value}",
        )
        self.terminal_status.setText(f"Shell open on {session.device_name} ({session.endpoint}).")
        device = self.services.devices.get(session.device_id)
        self.assistant_panel.set_context(
            f"Beside {session.device_name} - {session.endpoint}"
        )
        self.assistant_panel.set_environment(
            environment_for_os(device.operating_system if device else None)
        )
        self.assistant_panel.refresh()
        self._refresh_system_info()
        self.services.audit.record(
            "session",
            "terminal.opened",
            severity="info",
            outcome="success",
            device_id=session.device_id,
            subject=session.endpoint,
            detail={"route": session.route.value},
        )

    # -- system information -------------------------------------------------
    def _refresh_system_info(self) -> None:
        """Collect CPU/memory/storage/OS/uptime on a worker; never block the UI."""
        session_id = self.terminal_view.session_id
        session = self.services.sessions.get(session_id) if session_id else None
        if session is None or session.adapter is None:
            self.system_info.reset()
            return
        if session.state is not SessionState.CONNECTED:
            self.system_info.reset("The SSH session is no longer connected.")
            return
        if session.protocol != "ssh":
            self.system_info.reset("System information is available for SSH sessions only.")
            return
        self.system_info.set_loading(session.device_name)
        worker = Worker(collect_system_info, session.adapter)
        connect_worker(
            worker,
            on_result=lambda info: self._on_system_info(session, info),
            on_error=lambda message, detail: self._on_system_info_error(session, message, detail),
        )
        QThreadPool.globalInstance().start(worker)

    def _on_system_info(self, session: Session, info: object) -> None:
        if self.terminal_view.session_id != session.id:
            return  # the operator attached another host meanwhile
        if not isinstance(info, SystemInfo):
            self._on_system_info_error(session, "unexpected response from the host", "")
            return
        self.system_info.show_info(info, session.device_name)
        self.services.audit.record(
            "session",
            "sysinfo.collected",
            severity="info",
            outcome="success",
            device_id=session.device_id,
            subject=session.endpoint,
            detail={"hostname": info.hostname, "kernel": info.kernel, "cores": info.cpu_cores},
        )

    def _on_system_info_error(self, session: Session, message: str, detail: str = "") -> None:
        if self.terminal_view.session_id != session.id:
            return
        text = str(message) or "the host did not answer"
        self.system_info.show_error(text)
        self.services.audit.record(
            "session",
            "sysinfo.collected",
            severity="warning",
            outcome="failure",
            device_id=session.device_id,
            subject=session.endpoint,
            detail={"message": text, "detail": str(detail)[:400]},
        )

    def _on_terminal_closed(self, session_id: str) -> None:
        self.terminal_status.setText("Shell closed.")
        self.system_info.reset("Terminal closed - attach a shell to read system information.")
        self.assistant_panel.set_context("No terminal attached")
        if session_id:
            session = self.services.sessions.get(session_id)
            self.services.audit.record(
                "session",
                "terminal.closed",
                severity="info",
                outcome="success",
                device_id=session.device_id if session else None,
                detail={"session_id": session_id},
            )

    # ------------------------------------------------------------------ console
    def _build_console_tab(self) -> QWidget:
        page = QWidget()
        self.console_session_combo = QComboBox()
        self.console_session_combo.setMinimumWidth(240)
        self.console_environment = QComboBox()
        for environment in ("bash", "powershell", "cmd", "cisco", "panos"):
            self.console_environment.addItem(environment, environment)

        self.console_output = QPlainTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setObjectName("Mono")
        self.console_output.setMaximumBlockCount(5000)

        self.console_input = SuggestionLineEdit()
        self.console_input.setObjectName("Mono")
        self.console_input.setMinimumHeight(34)
        self.console_input.setPlaceholderText("Type a command - the rest is suggested from the catalog and your history")
        self.console_input.returnPressed.connect(self._on_console_return)
        self.console_input.textEdited.connect(self._update_completions)
        self.console_input.accepted.connect(lambda _text: self.completion_list.setCurrentRow(-1))
        self.console_input.navigate.connect(self._navigate_completions)
        self.completion_hint = QLabel("Tab or → completes · ↑ ↓ choose · Enter runs · Esc dismisses")
        self.completion_hint.setObjectName("Faint")
        self.console_environment.currentIndexChanged.connect(
            lambda: self._update_completions(self.console_input.text())
        )
        self.run_button = QPushButton("Run")
        self.run_button.setObjectName("Primary")
        self.run_button.clicked.connect(self.run_command)
        self.save_command_button = QPushButton("Save command…")
        self.save_command_button.setToolTip("Save the typed command to the Commands library")
        self.save_command_button.clicked.connect(self._save_console_command)

        self.completion_list = QListWidget()
        self.completion_list.setMaximumHeight(120)
        self.completion_list.itemDoubleClicked.connect(self._accept_completion)
        self.completion_list.itemClicked.connect(self._accept_completion)

        self.diagnosis_view = QPlainTextEdit()
        self.diagnosis_view.setReadOnly(True)
        self.diagnosis_view.setObjectName("Mono")
        self.diagnosis_view.setMaximumHeight(180)
        self.diagnosis_view.setPlaceholderText("Failures are explained here with likely causes and suggested checks.")
        self.run_diagnostic_button = QPushButton("Run diagnostics for this device")
        self.run_diagnostic_button.clicked.connect(self._request_diagnostics)

        command_row = QHBoxLayout()
        command_row.addWidget(self.console_input, 1)
        command_row.addWidget(self.run_button)
        command_row.addWidget(self.save_command_button)

        controls = QFormLayout()
        controls.addRow("Session", self.console_session_combo)
        controls.addRow("Environment", self.console_environment)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(controls)
        layout.addWidget(self.console_output, 3)
        layout.addLayout(command_row)
        layout.addWidget(self.completion_hint)
        layout.addWidget(self.completion_list)
        layout.addWidget(QLabel("Diagnosis"))
        layout.addWidget(self.diagnosis_view, 1)
        layout.addWidget(self.run_diagnostic_button)
        note = QLabel(
            "The console runs single commands through the policy gate and shows their output. For "
            "interactive programs, colours, escape sequences and PTY resizing use the Terminal tab; "
            "both tabs share the same SSH session."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _append_console(self, text: str) -> None:
        self.console_output.appendPlainText(text.rstrip("\n"))

    def _console_session(self) -> Session | None:
        session_id = self.console_session_combo.currentData()
        return self.services.sessions.get(session_id) if session_id else None

    def _current_environment(self) -> str:
        session = self._console_session()
        if session is None:
            return self.console_environment.currentData() or "unknown"
        device = self.services.devices.get(session.device_id)
        if device is None:
            return self.console_environment.currentData() or "unknown"
        return environment_for_os(device.operating_system) if device.operating_system != "unknown" else (
            self.console_environment.currentData() or "unknown"
        )

    def _update_completions(self, text: str) -> None:
        environment = self._current_environment()
        session = self._console_session()
        history = [
            record.command
            for record in self.services.history.recent(50, device_id=session.device_id if session else None)
        ]
        suggestions = self.services.autocomplete.suggest(text, environment, history=history, limit=8)
        self.completion_list.clear()
        for suggestion in suggestions:
            item = QListWidgetItem(suggestion.display())
            item.setData(Qt.ItemDataRole.UserRole, suggestion.text)
            if suggestion.source is SuggestionSource.AI_GENERATED:
                item.setForeground(Qt.GlobalColor.yellow)
            self.completion_list.addItem(item)
        self.completion_list.setCurrentRow(-1)
        # Preview the best match inline; the widget only shows it when it really
        # completes what has been typed.
        self.console_input.set_suggestion(suggestions[0].text if suggestions else None)

    def _accept_completion(self, item: QListWidgetItem) -> None:
        self.console_input.setText(str(item.data(Qt.ItemDataRole.UserRole)))
        self.console_input.setCursorPosition(len(self.console_input.text()))
        self.console_input.clear_suggestion()
        self.completion_list.setCurrentRow(-1)
        self.console_input.setFocus()

    def _navigate_completions(self, delta: int) -> None:
        count = self.completion_list.count()
        if not count:
            return
        row = self.completion_list.currentRow()
        row = 0 if row < 0 else (row + delta) % count
        self.completion_list.setCurrentRow(row)
        item = self.completion_list.item(row)
        # Up/Down preview the highlighted candidate; Enter then accepts it.
        self.console_input.set_suggestion(str(item.data(Qt.ItemDataRole.UserRole)))

    def _on_console_return(self) -> None:
        """Enter completes a highlighted candidate first, then runs the line."""
        item = self.completion_list.currentItem()
        if item is not None:
            text = str(item.data(Qt.ItemDataRole.UserRole))
            if text != self.console_input.text():
                self._accept_completion(item)
                return
        self.run_command()

    def _save_console_command(self) -> None:
        """Save the line currently typed in the console to the command library."""
        command = self.console_input.text().strip()
        if not command:
            QMessageBox.information(
                self, "Nothing to save", "Type the command you want to save first."
            )
            return
        dialog = CommandDialog(
            self.services,
            command=command,
            environment=self._current_environment(),
            parent=self,
        )
        if dialog.exec() and dialog.saved is not None:
            self.status_message.emit(
                f"Saved “{dialog.saved.name}” to Commands - find it in the Commands section."
            )

    def load_command(self, command: str, environment: str = "") -> None:
        """Put a saved command into the console input without running it."""
        self.tabs.setCurrentWidget(self.console_page)
        if environment:
            index = self.console_environment.findData(environment)
            if index >= 0:
                self.console_environment.setCurrentIndex(index)
        self.console_input.setText(command)
        self.console_input.setCursorPosition(len(command))
        self.console_input.setFocus()
        self._update_completions(command)

    def run_command(self) -> None:
        session = self._console_session()
        command = self.console_input.text().strip()
        if session is None:
            QMessageBox.information(self, "No session", "Connect to a device first.")
            return
        if not command:
            return
        environment = self._current_environment()
        decision = self.services.policy.evaluate(command, environment)
        if not decision.allowed:
            if decision.requires_confirmation:
                answer = QMessageBox.warning(
                    self,
                    f"Confirm {decision.risk.value} command",
                    f"{decision.reason}\n\nCommand:\n{command}\n\nRun it anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self._append_console(f"$ {command}\n[blocked by policy] {decision.reason}\n")
                    return
                approved = True
            else:
                self._append_console(f"$ {command}\n[blocked by policy] {decision.reason}\n")
                self.services.audit.record(
                    "policy",
                    "command.denied",
                    severity="warning",
                    outcome="denied",
                    device_id=session.device_id,
                    subject=command,
                    detail={"risk": decision.risk.value, "reason": decision.reason},
                )
                return
        else:
            approved = False
        self.console_input.clear()
        self.completion_list.clear()
        self._append_console(f"$ {command}")
        manager = self.services.sessions

        def finished(payload) -> None:
            result, _reason = payload
            if result is None:
                self._append_console("[blocked]")
                return
            if result.stdout:
                self._append_console(result.stdout.rstrip("\n"))
            if result.stderr:
                self._append_console(f"[stderr] {result.stderr.rstrip()}")
            self._append_console(f"[exit {result.exit_code}]\n")
            self._diagnose(result.combined(), environment, session.protocol)
            self._refresh_session_row(session)

        def failed(message: str, detail: str) -> None:
            self._append_console(f"[error] {detail}\n")
            self._diagnose(detail, environment, session.protocol)

        worker = Worker(manager.execute, session, command, approved=approved, environment=environment)
        connect_worker(worker, on_result=finished, on_error=failed)
        QThreadPool.globalInstance().start(worker)

    def _diagnose(self, output: str, environment: str, protocol: str) -> None:
        diagnosis = self.services.errors.diagnose(output, environment, protocol)
        if diagnosis is None:
            self.diagnosis_view.setPlainText("")
            return
        self.diagnosis_view.setPlainText(diagnosis.render())
        if self.services.history:
            self.services.history.record(
                f"#diagnosis: {diagnosis.title}",
                environment=environment,
                status="suggested",
                source="rules",
                notes=diagnosis.rule_id,
            )

    def _request_diagnostics(self) -> None:
        session = self._console_session()
        device = self.services.devices.get(session.device_id) if session else self.current_device()
        if device is None:
            return
        port = 0
        cfg = device.connection(device.primary_protocol())
        if cfg and cfg.port:
            port = int(cfg.port)
        self.diagnostic_requested.emit(device.hostname, port, device.primary_protocol() or "")

    # ------------------------------------------------------------------ files
    def _build_files_tab(self) -> QWidget:
        page = QWidget()
        self.file_session_combo = QComboBox()
        self.file_session_combo.setMinimumWidth(240)
        self.file_session_combo.currentIndexChanged.connect(self._on_files_session_changed)

        self.local_path_edit = QLineEdit(self.local_path)
        self.local_path_edit.returnPressed.connect(lambda: self.load_local(self.local_path_edit.text()))
        self.local_list = QListWidget()
        self.local_list.itemDoubleClicked.connect(self._local_activated)

        self.remote_path_edit = QLineEdit(".")
        self.remote_path_edit.returnPressed.connect(lambda: self.load_remote(self.remote_path_edit.text()))
        self.remote_list = QListWidget()
        self.remote_list.itemDoubleClicked.connect(self._remote_activated)

        local_controls = QHBoxLayout()
        local_controls.addWidget(QLabel("Local"))
        local_controls.addWidget(self.local_path_edit, 1)
        up_local = QPushButton("Up")
        up_local.clicked.connect(self._local_up)
        local_controls.addWidget(up_local)

        remote_controls = QHBoxLayout()
        remote_controls.addWidget(QLabel("Remote"))
        remote_controls.addWidget(self.remote_path_edit, 1)
        up_remote = QPushButton("Up")
        up_remote.clicked.connect(self._remote_up)
        refresh_remote = QPushButton("Refresh")
        refresh_remote.clicked.connect(lambda: self.load_remote(self.remote_path_edit.text()))
        remote_controls.addWidget(up_remote)
        remote_controls.addWidget(refresh_remote)

        self.upload_button = QPushButton("Upload →")
        self.upload_button.clicked.connect(self.upload_files)
        self.download_button = QPushButton("← Download")
        self.download_button.clicked.connect(self.download_files)
        self.mkdir_button = QPushButton("New folder")
        self.mkdir_button.clicked.connect(self.create_remote_folder)
        self.delete_remote_button = QPushButton("Delete remote")
        self.delete_remote_button.setObjectName("Danger")
        self.delete_remote_button.clicked.connect(self.delete_remote)

        transfer_buttons = QHBoxLayout()
        transfer_buttons.addWidget(self.upload_button)
        transfer_buttons.addWidget(self.download_button)
        transfer_buttons.addWidget(self.mkdir_button)
        transfer_buttons.addWidget(self.delete_remote_button)
        transfer_buttons.addStretch(1)

        self.transfer_progress = QProgressBar()
        self.transfer_progress.setValue(0)
        self.cancel_transfer_button = QPushButton("Cancel transfer")
        self.cancel_transfer_button.setEnabled(False)
        self.cancel_transfer_button.clicked.connect(self._cancel_transfer)
        self.transfer_status = QLabel("")
        self.transfer_status.setObjectName("Faint")

        panes = QSplitter(Qt.Orientation.Horizontal)
        local_pane = QWidget()
        local_layout = QVBoxLayout(local_pane)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.addLayout(local_controls)
        local_layout.addWidget(self.local_list, 1)
        remote_pane = QWidget()
        remote_layout = QVBoxLayout(remote_pane)
        remote_layout.setContentsMargins(0, 0, 0, 0)
        remote_layout.addLayout(remote_controls)
        remote_layout.addWidget(self.remote_list, 1)
        panes.addWidget(local_pane)
        panes.addWidget(remote_pane)

        session_form = QFormLayout()
        session_form.addRow("Remote session", self.file_session_combo)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(session_form)
        layout.addWidget(panes, 1)
        layout.addLayout(transfer_buttons)
        layout.addWidget(self.transfer_progress)
        layout.addWidget(self.cancel_transfer_button)
        layout.addWidget(self.transfer_status)
        note = QLabel(
            "Works over SFTP or an SMB 2/3 share. Transfers stream in 512 KB chunks and can resume: "
            "large files never load into memory. Remote-to-remote transfers use the connected session "
            "as the staging path."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._set_transfer_controls(False)
        self.load_local(self.local_path)
        return page

    def _refresh_remote_session_combo(self) -> None:
        if not hasattr(self, "file_session_combo"):
            return
        current = self.file_session_combo.currentData()
        self.file_session_combo.blockSignals(True)
        self.file_session_combo.clear()
        for session in self.services.sessions.active_sessions():
            if session.protocol in ("sftp", "smb") and session.state is SessionState.CONNECTED:
                self.file_session_combo.addItem(
                    f"{session.device_name} ({session.protocol.upper()} {session.endpoint})", session.id
                )
        index = self.file_session_combo.findData(current)
        if index >= 0:
            self.file_session_combo.setCurrentIndex(index)
        self.file_session_combo.blockSignals(False)
        self._set_transfer_controls(bool(self.file_session_combo.count()))
        selected = self.file_session_combo.currentData()
        if selected and selected != current:
            # The selection changed while signals were blocked: reset the pane
            # to that session's root and list it.
            self._on_files_session_changed()

    def _on_files_session_changed(self) -> None:
        session = self._remote_session()
        if session is None:
            return
        self.remote_path = self._remote_root(session)
        self.remote_path_edit.setText(self.remote_path)
        self.remote_list.clear()
        self.load_remote(self.remote_path)

    @staticmethod
    def _remote_root(session: Session | None) -> str:
        """SFTP starts at ``.``; an SMB share starts at the share root."""
        return "" if session is not None and session.protocol == "smb" else "."

    def _set_transfer_controls(self, enabled: bool) -> None:
        for widget in (
            self.upload_button,
            self.download_button,
            self.mkdir_button,
            self.delete_remote_button,
        ):
            widget.setEnabled(enabled)

    def _remote_session(self) -> Session | None:
        session_id = self.file_session_combo.currentData()
        return self.services.sessions.get(session_id) if session_id else None

    def load_local(self, path: str) -> None:
        target = Path(path or self.local_path)
        if not target.exists():
            target = Path.home()
        self.local_path = str(target)
        self.local_path_edit.setText(self.local_path)
        self.local_list.clear()
        try:
            entries = sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        except OSError as exc:
            self.transfer_status.setText(f"could not read {target}: {exc}")
            return
        for entry in entries:
            item = QListWidgetItem(("[ ] " if entry.is_dir() else "    ") + entry.name)
            item.setData(Qt.ItemDataRole.UserRole, str(entry))
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.is_dir())
            self.local_list.addItem(item)

    def _local_activated(self, item: QListWidgetItem) -> None:
        if item.data(Qt.ItemDataRole.UserRole + 1):
            self.load_local(str(item.data(Qt.ItemDataRole.UserRole)))

    def _local_up(self) -> None:
        self.load_local(str(Path(self.local_path).parent))

    def load_remote(self, path: str) -> None:
        session = self._remote_session()
        if session is None or session.adapter is None:
            self.transfer_status.setText("Connect an SFTP or SMB session to browse remote files.")
            return
        adapter = session.adapter
        try:
            entries = adapter.listdir(path)
        except ProtocolError as exc:
            self.transfer_status.setText(str(exc))
            return
        self.remote_path = path
        self.remote_path_edit.setText(path)
        self.remote_list.clear()
        for entry in entries:
            item = QListWidgetItem(_describe_remote_entry(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.path)
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.is_dir)
            self.remote_list.addItem(item)
        self.transfer_status.setText(
            f"{len(entries)} entries in {session.device_name} ({session.protocol.upper()}):{path or '/'}"
        )

    def _remote_activated(self, item: QListWidgetItem) -> None:
        if item.data(Qt.ItemDataRole.UserRole + 1):
            self.load_remote(str(item.data(Qt.ItemDataRole.UserRole)))

    def _remote_up(self) -> None:
        session = self._remote_session()
        root = self._remote_root(session)
        current = self.remote_path.replace("\\", "/").rstrip("/")
        if current in ("", "."):
            return
        if "/" in current:
            parent = current.rsplit("/", 1)[0] or ("/" if current.startswith("/") else root)
        else:
            parent = root
        self.load_remote(parent)

    def upload_files(self) -> None:  # pragma: no cover - needs a file dialog
        session = self._remote_session()
        if session is None or session.adapter is None:
            QMessageBox.information(self, "No remote session", "Connect an SFTP or SMB session first.")
            return
        selected = [item for item in self.local_list.selectedItems() if not item.data(Qt.ItemDataRole.UserRole + 1)]
        if selected:
            paths = [str(item.data(Qt.ItemDataRole.UserRole)) for item in selected]
        else:
            paths, _ = QFileDialog.getOpenFileNames(self, "Upload files", self.local_path)
            if not paths:
                return
        self._start_transfer("upload", paths)

    def download_files(self) -> None:
        session = self._remote_session()
        if session is None or session.adapter is None:
            QMessageBox.information(self, "No remote session", "Connect an SFTP or SMB session first.")
            return
        selected = [item for item in self.remote_list.selectedItems() if not item.data(Qt.ItemDataRole.UserRole + 1)]
        if not selected:
            QMessageBox.information(self, "No selection", "Select one or more remote files to download.")
            return
        for item in selected:
            remote = str(item.data(Qt.ItemDataRole.UserRole))
            local = str(Path(self.local_path) / Path(remote).name)
            self._start_transfer("download", [remote], local_override=local)

    def create_remote_folder(self) -> None:  # pragma: no cover - needs user input
        session = self._remote_session()
        if session is None or session.adapter is None:
            return
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name:
            target = f"{self.remote_path.rstrip('/')}/{name}"
            try:
                session.adapter.mkdir(target)
                self.load_remote(self.remote_path)
            except ProtocolError as exc:
                QMessageBox.warning(self, "Could not create folder", str(exc))

    def delete_remote(self) -> None:  # pragma: no cover - needs user input
        session = self._remote_session()
        if session is None or session.adapter is None:
            return
        selected = [item for item in self.remote_list.selectedItems()]
        if not selected:
            return
        target = str(selected[0].data(Qt.ItemDataRole.UserRole))
        if (
            QMessageBox.question(self, "Delete remote file", f"Delete {target} on {session.device_name}?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            if selected[0].data(Qt.ItemDataRole.UserRole + 1):
                session.adapter.rmdir(target)
            else:
                session.adapter.remove(target)
            self.services.audit.record(
                "transfer",
                "remote.delete",
                severity="notice",
                outcome="success",
                device_id=session.device_id,
                subject=target,
            )
            self.load_remote(self.remote_path)
        except ProtocolError as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))

    def _start_transfer(self, direction: str, items: list[str], local_override: str | None = None) -> None:
        session = self._remote_session()
        if session is None or session.adapter is None:
            return
        adapter = session.adapter
        remote_dir = self.remote_path
        self._transfer_protocol = session.protocol
        cancel_flag: list[bool] = []
        self._cancel_flags[session.id] = cancel_flag
        self.cancel_transfer_button.setEnabled(True)
        self.transfer_progress.setValue(0)
        worker = Worker(self._transfer_worker, adapter, direction, items, remote_dir, local_override, cancel_flag)
        worker.kwargs["progress_cb"] = worker.signals.progress.emit
        connect_worker(
            worker,
            on_result=self._on_transfer_done,
            on_error=self._on_transfer_failed,
            on_progress=self._on_transfer_progress,
        )
        QThreadPool.globalInstance().start(worker)
        self.transfer_status.setText(f"{direction} started for {len(items)} item(s)")

    @staticmethod
    def _transfer_worker(
        adapter,
        direction: str,
        items: list[str],
        remote_dir: str,
        local_override: str | None,
        cancel_flag: list[bool],
        progress_cb=None,
    ):
        cancel = (lambda: bool(cancel_flag)) if cancel_flag is not None else None
        results = []
        for item in items:
            if direction == "upload":
                remote_target = (
                    f"{remote_dir.rstrip('/')}/{Path(item).name}" if remote_dir not in (".", "") else Path(item).name
                )
                results.append(adapter.upload(item, remote_target, progress=progress_cb, cancel=cancel))
            else:
                local_target = local_override or str(Path.home() / Path(item).name)
                results.append(adapter.download(item, local_target, progress=progress_cb, cancel=cancel))
        return results

    def _on_transfer_progress(self, transferred: int, total: int) -> None:
        self.transfer_progress.setMaximum(max(total, 1))
        self.transfer_progress.setValue(transferred)
        self.transfer_status.setText(f"{transferred} / {total} bytes")

    def _on_transfer_done(self, results: object) -> None:
        self.cancel_transfer_button.setEnabled(False)
        for result in results:  # type: ignore[union-attr]
            self.transfer_status.setText(getattr(result, "message", "transfer finished"))
        self.load_remote(self.remote_path)

    def _on_transfer_failed(self, message: str, detail: str) -> None:
        self.cancel_transfer_button.setEnabled(False)
        self.transfer_status.setText(f"transfer failed: {message}")
        diagnosis = self.services.errors.diagnose(
            detail, "unknown", getattr(self, "_transfer_protocol", "sftp")
        )
        if diagnosis is not None:
            self.diagnosis_view.setPlainText(diagnosis.render())

    def _cancel_transfer(self) -> None:
        for flag in self._cancel_flags.values():
            flag.append(True)

    # ------------------------------------------------------------------ rdp
    def _build_rdp_tab(self) -> QWidget:
        page = QWidget()
        self.rdp_device_combo = QComboBox()
        self.rdp_device_combo.setMinimumWidth(240)
        self.rdp_width = QSpinBox()
        self.rdp_width.setRange(800, 7680)
        self.rdp_width.setValue(1920)
        self.rdp_height = QSpinBox()
        self.rdp_height.setRange(600, 4320)
        self.rdp_height.setValue(1080)
        self.rdp_fullscreen = QCheckBox("Full screen")
        self.rdp_clipboard = QCheckBox("Share clipboard")
        self.rdp_clipboard.setChecked(True)
        self.rdp_drives = QCheckBox("Redirect local drives")
        self.rdp_cert_ignore = QCheckBox("Ignore certificate")
        self.rdp_username = QLineEdit()
        self.rdp_username.setPlaceholderText("pre-filled username (the password is sent on stdin, never to disk)")
        self.rdp_password = QLineEdit()
        self.rdp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.rdp_password.setPlaceholderText("blank = use the stored credential (or the client asks)")

        self.rdp_launch_button = QPushButton("Open remote desktop")
        self.rdp_launch_button.setObjectName("Primary")
        self.rdp_launch_button.clicked.connect(self.launch_rdp)
        self.rdp_stop_button = QPushButton("Stop")
        self.rdp_stop_button.setObjectName("Danger")
        self.rdp_stop_button.clicked.connect(self.stop_rdp)

        # A native child window that FreeRDP is told to render into via
        # /parent-window. WA_NativeWindow forces a real HWND for winId().
        self.rdp_embed = QWidget()
        self.rdp_embed.setObjectName("RdpEmbed")
        self.rdp_embed.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.rdp_embed.setMinimumHeight(320)
        self.rdp_placeholder = QLabel(
            "The desktop session renders here. Nexus Remote drives FreeRDP directly - "
            "no external Remote Desktop window is opened."
        )
        self.rdp_placeholder.setObjectName("Faint")
        self.rdp_placeholder.setWordWrap(True)
        self.rdp_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_layout = QVBoxLayout(self.rdp_embed)
        placeholder_layout.setContentsMargins(12, 12, 12, 12)
        placeholder_layout.addWidget(self.rdp_placeholder)

        self.rdp_status = QLabel("No embedded session.")
        self.rdp_status.setObjectName("Faint")
        self.rdp_status.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Device", self.rdp_device_combo)
        form.addRow("Resolution", _row(self.rdp_width, self.rdp_height))
        form.addRow(
            "Options",
            _row(self.rdp_fullscreen, self.rdp_clipboard, self.rdp_drives, self.rdp_cert_ignore),
        )
        form.addRow("Username", self.rdp_username)
        form.addRow("Password", self.rdp_password)

        buttons = QHBoxLayout()
        buttons.addWidget(self.rdp_launch_button)
        buttons.addWidget(self.rdp_stop_button)
        buttons.addStretch(1)

        note = QLabel(
            "The session is performed by Nexus Remote itself using the FreeRDP client, embedded in this "
            "window (mstsc.exe is never used). FreeRDP must be installed: run packaging/install_freerdp.ps1 "
            "or put wlfreerdp.exe/sdl-freerdp.exe on PATH. The password is passed to the client on standard "
            "input, never on the command line or to disk. Certificates are trusted on first use unless "
            "'Ignore certificate' is ticked."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.rdp_embed, 1)
        layout.addWidget(self.rdp_status)
        layout.addWidget(note)
        return page

    def refresh_rdp_devices(self) -> None:
        current = self.rdp_device_combo.currentData()
        self.rdp_device_combo.clear()
        for device in self.services.devices.list(sort="name"):
            cfg = device.connection("rdp")
            label = f"{device.name}  ({device.hostname})" + ("" if cfg and cfg.enabled else "  - RDP disabled")
            self.rdp_device_combo.addItem(label, device.id)
        index = self.rdp_device_combo.findData(current)
        if index >= 0:
            self.rdp_device_combo.setCurrentIndex(index)

    def launch_rdp(self) -> None:
        device_id = self.rdp_device_combo.currentData()
        device = self.services.devices.get(device_id) if device_id else None
        if device is None:
            QMessageBox.information(self, "No device", "Select a device.")
            return
        # The GUI owns the HWND; the client window is re-parented into it.
        parent_window = int(self.rdp_embed.winId())
        username = self.rdp_username.text().strip() or None
        secret = self.rdp_password.text() or None
        stored_password = any(
            ref.kind == "password" for ref in self.services.credentials.references(device.id)
        )
        needs_prompt = secret is None and not stored_password
        try:
            session = self.services.sessions.open(
                device,
                "rdp",
                username=username,
                secret=secret,
                options={
                    "width": self.rdp_width.value(),
                    "height": self.rdp_height.value(),
                    "fullscreen": self.rdp_fullscreen.isChecked(),
                    "clipboard": self.rdp_clipboard.isChecked(),
                    "redirect_drives": self.rdp_drives.isChecked(),
                    "cert_ignore": self.rdp_cert_ignore.isChecked(),
                    "gateway": device.routing.gateway,
                    "parent_window": parent_window,
                },
            )
            self.rdp_password.clear()
            self._append_session(session)
            result = self.services.sessions.connect(session)
            self._refresh_session_row(result)
            if result.state is SessionState.CONNECTED:
                self.rdp_placeholder.hide()
                self.rdp_status.setText(
                    f"Starting the desktop session to {device.name} ({session.endpoint}) ..."
                )
                if needs_prompt:
                    self.rdp_status.setText(
                        f"Starting the desktop session to {device.name} ({session.endpoint}) ... "
                        "No password was entered or saved, so FreeRDP may open its own sign-in window."
                    )
                # The client window appears asynchronously; report the outcome.
                QTimer.singleShot(2500, lambda: self._report_rdp_embed(session))
            else:
                self.rdp_status.setText(result.error or "the FreeRDP client could not be started")
                QMessageBox.warning(self, "RDP", result.error or "the FreeRDP client could not be started")
        except ProtocolError as exc:
            self.rdp_password.clear()
            QMessageBox.warning(self, "RDP", str(exc))

    def _report_rdp_embed(self, session: Session) -> None:
        adapter = session.adapter
        if adapter is None or session.state is not SessionState.CONNECTED:
            return
        window = getattr(adapter, "embedded_window", 0)
        error = getattr(adapter, "embed_error", "")
        if window:
            self.rdp_status.setText(
                f"Desktop session to {session.endpoint} is running inside this window."
            )
        elif error:
            self.rdp_status.setText(f"{error}; the session may be in its own window.")

    def stop_rdp(self) -> None:
        """Force-stop any embedded desktop session (also used by Kill all)."""
        stopped = self._stop_rdp_embedded()
        self.status_message.emit(
            f"stopped {stopped} remote desktop session{'s' if stopped != 1 else ''}"
            if stopped
            else "no remote desktop session to stop"
        )

    def _stop_rdp_embedded(self) -> int:
        stopped = 0
        for session in self.services.sessions.sessions():
            if session.protocol == "rdp" and session.state is not SessionState.CLOSED:
                self.services.sessions.force_close(session)
                self._refresh_session_row(session)
                stopped += 1
        self.rdp_placeholder.show()
        self.rdp_status.setText("No embedded session.")
        return stopped


def _row(*widgets: QWidget) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return container
