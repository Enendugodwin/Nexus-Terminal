"""Security Center: credentials, host keys and the audit trail."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.security.redact import MASK
from nexus_remote.services import AppServices
from nexus_remote.ui.device_dialog import CredentialPromptDialog

SEVERITY_COLOURS = {
    "info": "#9aa5b8",
    "notice": "#4f8cff",
    "warning": "#efb04e",
    "critical": "#ff6b6b",
}


class SecurityCenter(QWidget):
    """Visibility and control over secrets, trust and audited activity."""

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_credentials_tab(), "Credentials")
        self.tabs.addTab(self._build_host_keys_tab(), "SSH host keys")
        self.tabs.addTab(self._build_audit_tab(), "Audit trail")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addWidget(self.tabs)
        self.refresh()

    # -- credentials --------------------------------------------------------
    def _build_credentials_tab(self) -> QWidget:
        page = QWidget()
        self.credentials_table = QTableWidget(0, 5)
        self.credentials_table.setHorizontalHeaderLabels(
            ["Device", "Kind", "Username", "Value in vault", "Stored at"]
        )
        _prepare(self.credentials_table)

        add_button = QPushButton("Add / replace…")
        add_button.setObjectName("Primary")
        add_button.clicked.connect(self.add_credential)
        revoke_button = QPushButton("Revoke selected")
        revoke_button.setObjectName("Danger")
        revoke_button.clicked.connect(self.revoke_credential)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        controls = QHBoxLayout()
        controls.addWidget(add_button)
        controls.addWidget(revoke_button)
        controls.addStretch(1)
        controls.addWidget(refresh_button)

        self.vault_label = QLabel("")
        self.vault_label.setObjectName("Faint")

        layout = QVBoxLayout(page)
        layout.addLayout(controls)
        layout.addWidget(self.credentials_table, 1)
        layout.addWidget(self.vault_label)
        layout.addWidget(
            _note(
                "The database stores a reference (device, kind, username) and the vault key name. Secret "
                "values live in the Windows Credential Manager and are never written to profiles, logs "
                f"or the audit trail - redaction masks such values as {MASK}."
            )
        )
        return page

    def add_credential(self) -> None:  # pragma: no cover - needs user input
        devices = self.services.devices.list(sort="name")
        if not devices:
            QMessageBox.information(self, "No devices", "Add a device first.")
            return
        from PySide6.QtWidgets import QInputDialog

        labels = [f"{device.name} ({device.hostname})" for device in devices]
        choice, ok = QInputDialog.getItem(self, "Store credential", "Device:", labels, editable=False)
        if not ok:
            return
        device = devices[labels.index(choice)]
        dialog = CredentialPromptDialog(self.services, device, self)
        if dialog.exec() == CredentialPromptDialog.DialogCode.Accepted:
            self.refresh()

    def revoke_credential(self) -> None:  # pragma: no cover - needs user input
        reference = self._selected_reference()
        if reference is None:
            QMessageBox.information(self, "No selection", "Select a credential reference to revoke.")
            return
        if (
            QMessageBox.question(
                self,
                "Revoke credential",
                f"Delete the stored {reference['kind']} for {reference['device_name']}"
                f"{' (' + reference['username'] + ')' if reference['username'] else ''} from the vault?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        target = next(
            (
                item
                for item in self.services.credentials.references()
                if item.id == reference["id"]
            ),
            None,
        )
        if target is not None:
            self.services.credentials.revoke(target)
            self.services.audit.record(
                "credential",
                "credential.revoked",
                severity="notice",
                outcome="success",
                device_id=target.device_id,
                subject=f"{target.kind} ({target.username or '-'})",
            )
        self.refresh()

    def _selected_reference(self) -> dict | None:
        rows = self.credentials_table.selectionModel().selectedRows() if self.credentials_table.selectionModel() else []
        if not rows:
            return None
        item = self.credentials_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # -- host keys ----------------------------------------------------------
    def _build_host_keys_tab(self) -> QWidget:
        page = QWidget()
        self.host_keys_table = QTableWidget(0, 5)
        self.host_keys_table.setHorizontalHeaderLabels(["Host", "Key type", "Fingerprint", "Status", "Source"])
        _prepare(self.host_keys_table)

        trust_button = QPushButton("Trust pending key")
        trust_button.setObjectName("Primary")
        trust_button.clicked.connect(self.trust_host_key)
        revoke_button = QPushButton("Revoke")
        revoke_button.setObjectName("Danger")
        revoke_button.clicked.connect(self.revoke_host_key)
        forget_button = QPushButton("Forget")
        forget_button.clicked.connect(self.forget_host_key)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        controls = QHBoxLayout()
        controls.addWidget(trust_button)
        controls.addWidget(revoke_button)
        controls.addWidget(forget_button)
        controls.addStretch(1)
        controls.addWidget(refresh_button)

        layout = QVBoxLayout(page)
        layout.addLayout(controls)
        layout.addWidget(self.host_keys_table, 1)
        layout.addWidget(
            _note(
                "Unknown host keys are refused until you accept them; a changed key is flagged as a possible "
                "interception. Trusting a key is always an explicit, audited decision."
            )
        )
        return page

    def _selected_host_key(self) -> dict | None:
        rows = self.host_keys_table.selectionModel().selectedRows() if self.host_keys_table.selectionModel() else []
        if not rows:
            return None
        item = self.host_keys_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def trust_host_key(self) -> None:  # pragma: no cover - needs user input
        entry = self._selected_host_key()
        if entry is None:
            return
        if entry["status"] == "trusted":
            QMessageBox.information(self, "Already trusted", "This key is already trusted.")
            return
        if (
            QMessageBox.question(
                self,
                "Trust host key",
                f"Trust the {entry['key_type']} key {entry['fingerprint']} presented by {entry['host']}?\n\n"
                "Only accept this after verifying the fingerprint with the device owner.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if self.services.host_keys.trust_pending(entry["host"], entry["key_type"]):
            self.services.audit.record(
                "host_key",
                "host_key.trusted",
                severity="notice",
                outcome="success",
                subject=f"{entry['host']} {entry['key_type']}",
                detail={"fingerprint": entry["fingerprint"]},
            )
        self.refresh()

    def revoke_host_key(self) -> None:  # pragma: no cover - needs user input
        entry = self._selected_host_key()
        if entry is None:
            return
        self.services.host_keys.revoke(entry["host"], entry["key_type"])
        self.services.audit.record(
            "host_key",
            "host_key.revoked",
            severity="warning",
            outcome="success",
            subject=f"{entry['host']} {entry['key_type']}",
            detail={"fingerprint": entry["fingerprint"]},
        )
        self.refresh()

    def forget_host_key(self) -> None:  # pragma: no cover - needs user input
        entry = self._selected_host_key()
        if entry is None:
            return
        self.services.host_keys.forget(entry["host"], entry["key_type"])
        self.refresh()

    # -- audit --------------------------------------------------------------
    def _build_audit_tab(self) -> QWidget:
        page = QWidget()
        self.severity_filter = QComboBox()
        self.severity_filter.addItem("all severities", None)
        for severity in ("info", "notice", "warning", "critical"):
            self.severity_filter.addItem(severity, severity)
        self.severity_filter.currentIndexChanged.connect(self.refresh)
        self.category_filter = QComboBox()
        self.category_filter.addItem("all categories", None)
        for category in ("credential", "session", "device", "policy", "host_key", "transfer", "assistant", "routing", "system"):
            self.category_filter.addItem(category, category)
        self.category_filter.currentIndexChanged.connect(self.refresh)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(20, 5000)
        self.limit_spin.setValue(200)
        self.limit_spin.valueChanged.connect(self.refresh)

        export_button = QPushButton("Export JSONL…")
        export_button.clicked.connect(self.export_audit)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Severity"))
        controls.addWidget(self.severity_filter)
        controls.addWidget(QLabel("Category"))
        controls.addWidget(self.category_filter)
        controls.addWidget(QLabel("Limit"))
        controls.addWidget(self.limit_spin)
        controls.addStretch(1)
        controls.addWidget(export_button)
        controls.addWidget(refresh_button)

        self.audit_table = QTableWidget(0, 6)
        self.audit_table.setHorizontalHeaderLabels(["Time", "Severity", "Category", "Action", "Subject", "Device"])
        _prepare(self.audit_table)

        layout = QVBoxLayout(page)
        layout.addLayout(controls)
        layout.addWidget(self.audit_table, 1)
        layout.addWidget(
            _note(
                "Every entry is redacted before it is stored, and also appended to "
                "logs/audit.jsonl so activity can be reviewed outside the application."
            )
        )
        return page

    def export_audit(self) -> None:  # pragma: no cover - needs a file dialog
        default = str(Path(self.services.paths.exports) / "audit-export.jsonl")
        path, _ = QFileDialog.getSaveFileName(self, "Export audit trail", default, "JSON Lines (*.jsonl)")
        if not path:
            return
        Path(path).write_text(
            self.services.audit.as_jsonl(
                limit=self.limit_spin.value(),
                category=self.category_filter.currentData(),
                severity=self.severity_filter.currentData(),
            ),
            encoding="utf-8",
        )
        QMessageBox.information(self, "Export complete", f"Audit events written to:\n{path}")

    # -- refresh ------------------------------------------------------------
    def refresh(self) -> None:
        device_names = {device.id: device.name for device in self.services.devices.list()}

        references = self.services.credentials.references()
        self.credentials_table.setRowCount(len(references))
        for row, reference in enumerate(references):
            stored = self.services.credentials.store.has(reference.key_name)
            values = [
                device_names.get(reference.device_id, reference.device_id or "-"),
                reference.kind,
                reference.username or "-",
                "present" if stored else "missing",
                reference.created_at.replace("T", " "),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        {
                            "id": reference.id,
                            "kind": reference.kind,
                            "username": reference.username,
                            "device_name": device_names.get(reference.device_id, "device"),
                        },
                    )
                if column == 3 and not stored:
                    item.setForeground(QColor(SEVERITY_COLOURS["warning"]))
                self.credentials_table.setItem(row, column, item)
        self.vault_label.setText(
            f"Vault backend: {self.services.credentials.store.backend_name}"
            + ("" if self.services.credentials.store.persistent else "  - not persistent on this system")
            + f"   |   {len(references)} reference(s)"
        )

        entries = self.services.host_keys.entries()
        self.host_keys_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [entry["host"], entry["key_type"], entry["fingerprint"], entry["status"], entry["source"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, dict(entry))
                if column == 3:
                    colour = {
                        "trusted": "#37c98b",
                        "pending": "#efb04e",
                        "revoked": "#ff6b6b",
                    }.get(str(value), "#9aa5b8")
                    item.setForeground(QColor(colour))
                self.host_keys_table.setItem(row, column, item)

        events = self.services.audit.events(
            limit=self.limit_spin.value(),
            category=self.category_filter.currentData(),
            severity=self.severity_filter.currentData(),
        )
        self.audit_table.setRowCount(len(events))
        for row, event in enumerate(events):
            values = [
                event.ts.replace("T", " "),
                event.severity,
                event.category,
                event.action,
                event.subject,
                device_names.get(event.device_id, "") if event.device_id else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 1:
                    item.setForeground(QColor(SEVERITY_COLOURS.get(event.severity, "#9aa5b8")))
                self.audit_table.setItem(row, column, item)


def _prepare(table: QTableWidget) -> None:
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setAlternatingRowColors(True)


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Faint")
    label.setWordWrap(True)
    return label
