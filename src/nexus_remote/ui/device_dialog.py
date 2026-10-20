"""Device profile editor and credential prompt dialogs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.devices.models import (
    AUTH_METHODS,
    CRITICALITIES,
    DEFAULT_PORTS,
    ENVIRONMENTS,
    OPERATING_SYSTEMS,
    PROTOCOLS,
    ROUTE_METHODS,
    Device,
    ProtocolConfig,
    RoutingPreferences,
    ValidationError,
    is_serial_port,
)
from nexus_remote.protocols.serial import (
    BAUD_RATES,
    DATA_BITS,
    DEFAULT_BAUD,
    FLOWS,
    PARITIES,
    STOP_BITS,
    available_serial_ports,
)
from nexus_remote.security.credentials import CREDENTIAL_KINDS
from nexus_remote.services import AppServices

EDITABLE_PROTOCOLS = ("ssh", "sftp", "rdp", "smb")


def _smb_note() -> str:
    from nexus_remote.protocols.smb import smb_available

    availability = (
        "The optional 'smbprotocol' package is installed."
        if smb_available()
        else "The optional 'smbprotocol' package is not installed (pip install \"nexus-remote[smb]\")."
    )
    return "SMB talks SMB 2/3 only and never falls back to SMB1; a share name is required. " + availability


AUTH_LABELS = {
    "credential_vault": "Stored credential (OS vault)",
    "ssh_key": "SSH private key (vault)",
    "ssh_agent": "SSH agent",
    "keyboard_interactive": "Keyboard-interactive prompt",
    "password_prompt": "Ask for password each time",
    "none": "No authentication",
}


class DeviceDialog(QDialog):
    """Create or edit a device profile, including its connection profiles."""

    def __init__(self, services: AppServices, device: Device | None = None, parent: QDialog | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self.existing = device
        self.setWindowTitle("Edit device" if device else "Add device")
        self.setMinimumWidth(640)
        self.setMinimumHeight(360)

        self.name_edit = QLineEdit(device.name if device else "")
        self.host_edit = QLineEdit(device.hostname if device else "")
        self.os_combo = _combo(OPERATING_SYSTEMS, device.operating_system if device else "linux")
        self.env_combo = _combo(ENVIRONMENTS, device.environment if device else "unspecified")
        self.crit_combo = _combo(CRITICALITIES, device.criticality if device else "standard")
        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.addItem("")
        for group in services.devices.list_groups():
            self.group_combo.addItem(group.name)
        self.group_combo.setCurrentText(device.group_name if device else "")
        self.tags_edit = QLineEdit(", ".join(device.tags) if device else "")
        self.owner_edit = QLineEdit(device.owner if device else "")
        self.description_edit = QLineEdit(device.description if device else "")
        self.notes_edit = QPlainTextEdit(device.notes if device else "")
        self.notes_edit.setFixedHeight(64)
        self.favorite_check = QCheckBox("Pin to favourites")
        self.favorite_check.setChecked(bool(device.favorite) if device else False)

        identity = QFormLayout()
        identity.addRow("Name", self.name_edit)
        identity.addRow("Hostname / IP", self.host_edit)
        identity.addRow("Operating system", self.os_combo)
        identity.addRow("Environment", self.env_combo)
        identity.addRow("Criticality", self.crit_combo)
        identity.addRow("Group", self.group_combo)
        identity.addRow("Tags (comma separated)", self.tags_edit)
        identity.addRow("Owner", self.owner_edit)
        identity.addRow("Description", self.description_edit)
        identity.addRow("Notes", self.notes_edit)

        # -- connections ----------------------------------------------------
        self._protocol_widgets: dict[str, tuple[QCheckBox, QSpinBox, QComboBox, QLineEdit, QLineEdit]] = {}
        connections_layout = QGridLayout()
        connections_layout.addWidget(QLabel("Protocol"), 0, 0)
        connections_layout.addWidget(QLabel("Enabled"), 0, 1)
        connections_layout.addWidget(QLabel("Port"), 0, 2)
        connections_layout.addWidget(QLabel("Authentication"), 0, 3)
        connections_layout.addWidget(QLabel("Username"), 0, 4)
        connections_layout.addWidget(QLabel("SMB share"), 0, 5)
        for row, protocol in enumerate(EDITABLE_PROTOCOLS, start=1):
            cfg = device.connection(protocol) if device else ProtocolConfig(protocol=protocol, enabled=protocol in ("ssh", "sftp"))
            enabled = QCheckBox()
            enabled.setChecked(bool(cfg.enabled) if cfg else False)
            port = QSpinBox()
            port.setRange(1, 65535)
            port.setValue(int(cfg.port) if cfg and cfg.port else DEFAULT_PORTS.get(protocol, 22))
            auth = _combo(AUTH_METHODS, cfg.auth_method if cfg else "credential_vault", AUTH_LABELS)
            username = QLineEdit(cfg.username if cfg else "")
            share = QLineEdit(str((cfg.extra or {}).get("share", "")) if cfg else "")
            share.setPlaceholderText("required for SMB, e.g. Data")
            share.setEnabled(protocol == "smb")
            if protocol != "smb":
                share.setVisible(False)
            connections_layout.addWidget(QLabel(protocol.upper()), row, 0)
            connections_layout.addWidget(enabled, row, 1)
            connections_layout.addWidget(port, row, 2)
            connections_layout.addWidget(auth, row, 3)
            connections_layout.addWidget(username, row, 4)
            connections_layout.addWidget(share, row, 5)
            self._protocol_widgets[protocol] = (enabled, port, auth, username, share)

        # -- SSH private keys ------------------------------------------------
        key_prefill = ""
        if device:
            for protocol in ("ssh", "sftp"):
                cfg = device.connection(protocol)
                if cfg and cfg.auth_method == "ssh_key":
                    key_prefill = str((cfg.extra or {}).get("key_filename", "") or "")
                    break
        self.key_edit = QLineEdit(key_prefill)
        self.key_edit.setPlaceholderText(r"C:\Users\you\.ssh\id_ed25519  or  ~/.ssh/id_rsa")
        browse_key = QPushButton("Browse…")
        browse_key.clicked.connect(self._browse_key)
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(browse_key)
        key_form = QFormLayout()
        key_form.addRow("Key file", key_row)
        key_note = _note(
            "Used when an SSH or SFTP connection's authentication is 'SSH private key (vault)'. "
            "Leave it empty to use a key stored in the Security Center; a passphrase-protected "
            "key reads its passphrase from the vault (kind 'ssh_key_passphrase')."
        )

        # -- serial console --------------------------------------------------
        serial_cfg = device.connection("serial") if device else None
        serial_extra = dict(serial_cfg.extra) if serial_cfg else {}
        self.serial_enabled = QCheckBox("Enable serial console (COM port or tty)")
        self.serial_enabled.setChecked(bool(serial_cfg and serial_cfg.enabled))
        self.serial_port_combo = QComboBox()
        self.serial_port_combo.setEditable(True)
        self.serial_port_combo.setMinimumWidth(230)
        self.serial_port_combo.setToolTip("Detected ports are listed; type any COM3 or /dev/ttyUSB0 name.")
        for port_name, description in available_serial_ports():
            label = f"{port_name} - {description}" if description else port_name
            self.serial_port_combo.addItem(label, port_name)
        prefill_port = str(serial_extra.get("port_name") or "")
        if not prefill_port and device and is_serial_port(device.hostname):
            prefill_port = device.hostname
        if prefill_port:
            index = self.serial_port_combo.findData(prefill_port)
            if index >= 0:
                self.serial_port_combo.setCurrentIndex(index)
            else:
                self.serial_port_combo.setEditText(prefill_port)

        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        for rate in BAUD_RATES:
            self.baud_combo.addItem(str(rate), rate)
        self.baud_combo.setCurrentText(str(serial_extra.get("baud") or DEFAULT_BAUD))

        self.data_bits_combo = _combo(tuple(str(value) for value in DATA_BITS), str(serial_extra.get("data_bits", 8)))
        self.parity_combo = _combo(PARITIES, str(serial_extra.get("parity", "none")))
        self.stop_bits_combo = _combo(STOP_BITS, str(serial_extra.get("stop_bits", "1")))
        self.flow_combo = _combo(FLOWS, str(serial_extra.get("flow", "none")))

        serial_form = QFormLayout()
        serial_form.addRow("Port", self.serial_port_combo)
        serial_form.addRow("Baud rate", self.baud_combo)
        serial_form.addRow("Data bits", self.data_bits_combo)
        serial_form.addRow("Parity", self.parity_combo)
        serial_form.addRow("Stop bits", self.stop_bits_combo)
        serial_form.addRow("Flow control", self.flow_combo)
        serial_note = _note(
            "A serial profile is a raw console: no login and no command policy gate - whatever you "
            "type goes straight to the device. The port field above is independent of Hostname/IP; "
            "if Hostname/IP is empty it is filled with the serial port name."
        )

        # -- routing --------------------------------------------------------
        routing = device.routing if device else RoutingPreferences()
        self.route_combo = _combo(ROUTE_METHODS, routing.preferred_method)
        self.fallback_checks: dict[str, QCheckBox] = {}
        fallback_layout = QHBoxLayout()
        for method in ROUTE_METHODS:
            if method == "direct":
                continue
            check = QCheckBox(method)
            check.setChecked(method in routing.fallbacks)
            self.fallback_checks[method] = check
            fallback_layout.addWidget(check)
        self.jump_edit = QLineEdit(routing.jump_host or "")
        self.jump_edit.setPlaceholderText("user@jump-host:22")
        self.gateway_edit = QLineEdit(routing.gateway or "")
        self.relay_edit = QLineEdit(routing.relay or "")
        self.agent_edit = QLineEdit(routing.agent_id or "")

        routing_form = QFormLayout()
        routing_form.addRow("Preferred route", self.route_combo)
        routing_form.addRow("Configured alternatives", _wrap(fallback_layout))
        routing_form.addRow("Jump host", self.jump_edit)
        routing_form.addRow("RDP gateway", self.gateway_edit)
        routing_form.addRow("Relay", self.relay_edit)
        routing_form.addRow("Endpoint agent id", self.agent_edit)

        credentials_button = QPushButton("Manage stored credentials…")
        credentials_button.setEnabled(device is not None)
        credentials_button.setToolTip(
            "Credentials are stored in the Windows Credential Manager; the database keeps only a reference."
            if device
            else "Save the device first, then store its credential."
        )
        credentials_button.clicked.connect(self._open_credentials)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        # The profile form is tall (identity + four protocols + routing). Put it
        # in a scroll area so the dialog always fits on small screens and the
        # Save/Cancel row stays reachable instead of falling below the desktop.
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        body_layout.addWidget(_group("Identity", identity))
        body_layout.addWidget(_group("Connection profiles", _wrap(connections_layout)))
        body_layout.addWidget(_group("SSH private key", key_form))
        body_layout.addWidget(key_note)
        body_layout.addWidget(_group("Serial console", serial_form))
        body_layout.addWidget(serial_note)
        body_layout.addWidget(_note(_smb_note()))
        body_layout.addWidget(_group("Routing", routing_form))
        body_layout.addWidget(credentials_button)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)

        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(
                min(900, max(640, available.width() - 160)),
                min(900, max(360, available.height() - 120)),
            )
        else:
            self.resize(820, 720)

    # -- helpers ------------------------------------------------------------
    def _open_credentials(self) -> None:  # pragma: no cover - needs user input
        if self.existing is None:
            return
        dialog = CredentialPromptDialog(self.services, self.existing, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(self, "Credential stored", "The secret is stored in the OS credential vault.")

    def _browse_key(self) -> None:
        directory = str(Path.home() / ".ssh")
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Choose an SSH private key",
            directory,
            "All files (*)",
        )
        if path:
            self.key_edit.setText(path)

    def selected_serial_port(self) -> str:
        """The chosen port, whether picked from the list or typed by hand."""
        text = self.serial_port_combo.currentText().strip()
        data = self.serial_port_combo.currentData()
        index = self.serial_port_combo.currentIndex()
        if data and index >= 0 and text == self.serial_port_combo.itemText(index):
            return str(data).strip()
        return text

    def _on_accept(self) -> None:  # pragma: no cover - needs user input
        try:
            self._build_device()
        except ValidationError as exc:
            QMessageBox.warning(self, "Invalid device profile", str(exc))
            return
        self.accept()

    def _build_device(self) -> Device:
        connections: dict[str, ProtocolConfig] = {}
        for protocol, (enabled, port, auth, username, share) in self._protocol_widgets.items():
            previous = self.existing.connection(protocol) if self.existing else None
            extra = dict(previous.extra) if previous else {}
            if protocol == "smb":
                value = share.text().strip().strip("\\/")
                if value:
                    extra["share"] = value
                else:
                    extra.pop("share", None)
            connections[protocol] = ProtocolConfig(
                protocol=protocol,
                enabled=enabled.isChecked(),
                port=port.value(),
                auth_method=auth.currentData() or auth.currentText(),
                username=username.text().strip(),
                extra=extra,
            )
        serial_port = self.selected_serial_port()
        serial_enabled = self.serial_enabled.isChecked()
        if serial_enabled and not serial_port:
            raise ValidationError(
                "enter a serial port for the serial console (for example COM3 or /dev/ttyUSB0)"
            )
        baud_text = self.baud_combo.currentText().strip() or str(DEFAULT_BAUD)
        try:
            baud = int(baud_text)
        except ValueError as exc:
            raise ValidationError("the serial baud rate must be a whole number") from exc
        if baud <= 0:
            raise ValidationError("the serial baud rate must be positive")
        connections["serial"] = ProtocolConfig(
            protocol="serial",
            enabled=serial_enabled,
            auth_method="none",
            extra={
                "port_name": serial_port,
                "baud": baud,
                "data_bits": int(self.data_bits_combo.currentText() or 8),
                "parity": self.parity_combo.currentText(),
                "stop_bits": self.stop_bits_combo.currentText(),
                "flow": self.flow_combo.currentText(),
            },
        )

        key_path = self.key_edit.text().strip()
        for protocol in ("ssh", "sftp"):
            cfg = connections.get(protocol)
            if cfg is None:
                continue
            if cfg.auth_method == "ssh_key" and key_path:
                cfg.extra["key_filename"] = key_path
            else:
                # A stale path must not silently override another auth method.
                cfg.extra.pop("key_filename", None)
        device = self.existing or Device(name="", hostname="")
        device.name = self.name_edit.text().strip()
        device.hostname = self.host_edit.text().strip() or (serial_port if serial_enabled else "")
        device.operating_system = self.os_combo.currentText()
        device.environment = self.env_combo.currentText()
        device.criticality = self.crit_combo.currentText()
        device.tags = [tag.strip() for tag in self.tags_edit.text().split(",") if tag.strip()]
        device.owner = self.owner_edit.text().strip()
        device.description = self.description_edit.text().strip()
        device.notes = self.notes_edit.toPlainText()
        device.favorite = self.favorite_check.isChecked()
        device.group_name = self.group_combo.currentText().strip()
        device.connections = connections
        device.routing = RoutingPreferences(
            preferred_method=self.route_combo.currentText(),
            fallbacks=[name for name, check in self.fallback_checks.items() if check.isChecked()],
            jump_host=self.jump_edit.text().strip() or None,
            gateway=self.gateway_edit.text().strip() or None,
            relay=self.relay_edit.text().strip() or None,
            agent_id=self.agent_edit.text().strip() or None,
        )
        return device.validate()

    def device(self) -> Device:
        return self._build_device()

    def selected_group(self) -> str:
        return self.group_combo.currentText().strip()


class CredentialPromptDialog(QDialog):
    """Store a secret in the OS vault and record only a reference."""

    def __init__(self, services: AppServices, device: Device, parent: QDialog | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self.device = device
        self.setWindowTitle(f"Credentials for {device.name}")
        self.setMinimumWidth(520)

        self.kind_combo = _combo(CREDENTIAL_KINDS, "password")
        self.username_edit = QLineEdit(
            (device.primary_protocol() and (device.connection(device.primary_protocol()).username if device.primary_protocol() else "")) or ""
        )
        self.secret_edit = QPlainTextEdit()
        self.secret_edit.setFixedHeight(120)
        self.secret_edit.setPlaceholderText("Password, token or private key contents (stored in the OS vault)")
        backend = QLabel(
            f"Vault backend: {services.credentials.store.backend_name}"
            + ("" if services.credentials.store.persistent else "  - not persistent on this system")
        )
        backend.setObjectName("Faint")

        form = QFormLayout()
        form.addRow("Kind", self.kind_combo)
        form.addRow("Username", self.username_edit)
        form.addRow("Secret", self.secret_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(backend)
        layout.addWidget(
            _note(
                "The secret is written to the OS credential vault. Device profiles, exports and logs "
                "never contain it; the audit trail records only that a credential was stored."
            )
        )
        layout.addWidget(buttons)

    def _save(self) -> None:  # pragma: no cover - needs user input
        secret = self.secret_edit.toPlainText()
        if not secret:
            QMessageBox.warning(self, "Nothing to store", "Enter the secret to store.")
            return
        kind = str(self.kind_combo.currentData() or self.kind_combo.currentText())
        username = self.username_edit.text().strip()
        self.services.credentials.save(self.device.id, kind, username, secret)
        self.services.audit.record(
            "credential",
            "credential.stored",
            outcome="success",
            device_id=self.device.id,
            subject=f"{kind} for {self.device.name}",
            detail={"username": username, "backend": self.services.credentials.store.backend_name},
        )
        self.accept()


def _combo(values: tuple[str, ...] | list[str], current: str, labels: dict[str, str] | None = None) -> QComboBox:
    combo = QComboBox()
    for value in values:
        combo.addItem((labels or {}).get(value, value), value)
    index = combo.findData(current)
    if index >= 0:
        combo.setCurrentIndex(index)
    return combo


def _group(title: str, layout: QFormLayout | QGridLayout | QVBoxLayout) -> QGroupBox:
    box = QGroupBox(title)
    box.setLayout(layout)
    return box


def _wrap(layout) -> QVBoxLayout:
    container = QVBoxLayout()
    container.addLayout(layout)
    return container


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("Muted")
    label.setAlignment(Qt.AlignmentFlag.AlignLeft)
    return label
