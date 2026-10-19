"""Settings: policy, routing restrictions, optional AI and data locations."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nexus_remote import config
from nexus_remote.assistant.model_adapters import NullModel, OllamaModel
from nexus_remote.services import AppServices


class SettingsPage(QWidget):
    """Preferences that shape policy and connectivity behaviour."""

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self.setting = services.database.get_setting

        self.allow_destructive = QCheckBox("Allow destructive commands (still requires confirmation)")
        self.confirm_state_changes = QCheckBox("Require confirmation for state-changing commands")
        self.allow_intrusive = QCheckBox("Allow intrusive tools (credential attacks, vulnerability scripts)")
        self.relay_edit = QLineEdit()
        self.relay_edit.setPlaceholderText("10.0.0.0/24, bastion.example.com:22, *.corp.example.com")
        self.ai_enabled = QCheckBox("Use a local Ollama-compatible model for optional explanations")
        self.ai_host = QLineEdit()
        self.ai_model = QLineEdit()
        save_button = QPushButton("Save settings")
        save_button.setObjectName("Primary")
        save_button.clicked.connect(self.save)

        policy_group = QGroupBox("Policy")
        policy_layout = QVBoxLayout(policy_group)
        policy_layout.addWidget(self.confirm_state_changes)
        policy_layout.addWidget(self.allow_destructive)
        policy_layout.addWidget(self.allow_intrusive)

        routing_group = QGroupBox("Relay and gateway restrictions")
        routing_layout = QFormLayout(routing_group)
        routing_layout.addRow("Allowed relay destinations", self.relay_edit)

        ai_group = QGroupBox("Optional AI")
        ai_layout = QFormLayout(ai_group)
        ai_layout.addRow(self.ai_enabled)
        ai_layout.addRow("Endpoint", self.ai_host)
        ai_layout.addRow("Model", self.ai_model)

        storage_group = QGroupBox("Local storage")
        storage_layout = QHBoxLayout(storage_group)
        self.paths_label = QLabel()
        self.paths_label.setObjectName("Faint")
        self.paths_label.setWordWrap(True)
        open_folder = QPushButton("Open data folder")
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.services.paths.root))))
        open_logs = QPushButton("Open logs")
        open_logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.services.paths.logs))))
        storage_layout.addWidget(self.paths_label, 1)
        storage_layout.addWidget(open_folder)
        storage_layout.addWidget(open_logs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(policy_group)
        layout.addWidget(routing_group)
        layout.addWidget(ai_group)
        layout.addWidget(storage_group)
        layout.addWidget(save_button)
        layout.addWidget(
            _note(
                f"{config.APP_NAME} {config.APP_VERSION} - AI is optional and never authoritative. Core "
                "features (inventory, SSH/SFTP, diagnostics, memory, audit) work with no network and no model."
            )
        )
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        self.allow_destructive.setChecked(self.setting("policy.allow_destructive", "0") == "1")
        self.confirm_state_changes.setChecked(self.setting("policy.confirm_state_changes", "1") == "1")
        self.allow_intrusive.setChecked(self.setting("policy.allow_intrusive", "0") == "1")
        self.relay_edit.setText(self.setting("relay.allowed_destinations", "") or "")
        self.ai_enabled.setChecked(self.setting("ai.enabled", "0") == "1")
        self.ai_host.setText(self.setting("ai.host", "http://127.0.0.1:11434") or "")
        self.ai_model.setText(self.setting("ai.model", "llama3.1") or "")
        self.paths_label.setText(
            f"Database: {self.services.paths.database}\nLogs: {self.services.paths.logs}\n"
            f"Exports: {self.services.paths.exports}"
        )

    def save(self) -> None:
        database = self.services.database
        database.set_setting("policy.allow_destructive", "1" if self.allow_destructive.isChecked() else "0")
        database.set_setting(
            "policy.confirm_state_changes", "1" if self.confirm_state_changes.isChecked() else "0"
        )
        database.set_setting("policy.allow_intrusive", "1" if self.allow_intrusive.isChecked() else "0")
        destinations = [item.strip() for item in self.relay_edit.text().split(",") if item.strip()]
        database.set_setting("relay.allowed_destinations", ",".join(destinations))

        self.services.policy.allow_destructive_commands = self.allow_destructive.isChecked()
        self.services.policy.require_confirmation_for_state_changes = self.confirm_state_changes.isChecked()
        self.services.policy.allow_intrusive_tools = self.allow_intrusive.isChecked()
        self.services.policy.relay_allowed_destinations = destinations

        database.set_setting("ai.enabled", "1" if self.ai_enabled.isChecked() else "0")
        database.set_setting("ai.host", self.ai_host.text().strip())
        database.set_setting("ai.model", self.ai_model.text().strip())
        if self.ai_enabled.isChecked():
            self.services.models.configure(
                OllamaModel(model=self.ai_model.text().strip() or "llama3.1", host=self.ai_host.text().strip())
            )
        else:
            self.services.models.configure(NullModel())

        self.services.audit.record(
            "policy",
            "settings.saved",
            severity="notice",
            outcome="success",
            detail={
                "allow_destructive": self.allow_destructive.isChecked(),
                "allow_intrusive": self.allow_intrusive.isChecked(),
                "relay_destinations": destinations,
                "ai_enabled": self.ai_enabled.isChecked(),
            },
        )
        QMessageBox.information(self, "Settings saved", "Policy and integration settings were updated.")


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Faint")
    label.setWordWrap(True)
    return label
