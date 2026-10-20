"""Device profile editor and credential prompt dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
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

        layout = QVBoxLayout(self)
        layout.addWidget(_group("Identity", identity))
        layout.addWidget(_group("Connection profiles", _wrap(connections_layout)))
        layout.addWidget(_note(_smb_note()))
        layout.addWidget(_group("Routing", routing_form))
        layout.addWidget(credentials_button)
        layout.addWidget(buttons)

    # -- helpers ------------------------------------------------------------
    def _open_credentials(self) -> None:  # pragma: no cover - needs user input
        if self.existing is None:
            return
        dialog = CredentialPromptDialog(self.services, self.existing, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(self, "Credential stored", "The secret is stored in the OS credential vault.")

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
        device = self.existing or Device(name="", hostname="")
        device.name = self.name_edit.text().strip()
        device.hostname = self.host_edit.text().strip()
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
