"""Settings: policy, routing restrictions, appearance, updates and AI."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nexus_remote import config
from nexus_remote.assistant.model_adapters import NullModel, OllamaModel
from nexus_remote.services import AppServices
from nexus_remote.ui.tasks import run_async
from nexus_remote.ui.theme import current_theme, theme_choices
from nexus_remote.updates import UpdateChecker, UpdateCheckResult, launch_installer


class SettingsPage(QWidget):
    """Preferences that shape policy, appearance, updates and connectivity."""

    def __init__(
        self,
        services: AppServices,
        parent: QWidget | None = None,
        *,
        on_theme_changed=None,
    ) -> None:
        super().__init__(parent)
        self.services = services
        self.setting = services.database.get_setting
        self._on_theme_changed = on_theme_changed

        self.allow_destructive = QCheckBox("Allow destructive commands (still requires confirmation)")
        self.confirm_state_changes = QCheckBox("Require confirmation for state-changing commands")
        self.allow_intrusive = QCheckBox("Allow intrusive tools (credential attacks, vulnerability scripts)")
        self.relay_edit = QLineEdit()
        self.relay_edit.setPlaceholderText("10.0.0.0/24, bastion.example.com:22, *.corp.example.com")

        self.theme_combo = QComboBox()
        for key, label in theme_choices():
            self.theme_combo.addItem(label, key)
        self.theme_combo.currentIndexChanged.connect(self._theme_selected)

        self.update_feed = QLineEdit()
        self.update_feed.setPlaceholderText("https://downloads.example.com/nexus/latest.json")
        self.update_auto = QCheckBox("Check for updates automatically after launch")
        self.update_status = QLabel("")
        self.update_status.setObjectName("Faint")
        self.update_status.setWordWrap(True)
        self.check_updates_button = QPushButton("Check for updates now")
        self.check_updates_button.clicked.connect(self.check_for_updates)
        self.version_label = QLabel(f"Installed version: {config.APP_VERSION}")
        self.version_label.setObjectName("Faint")

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

        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_group)
        appearance_layout.addRow("Theme", self.theme_combo)

        updates_group = QGroupBox("Application updates")
        updates_layout = QFormLayout(updates_group)
        updates_layout.addRow("Update feed URL", self.update_feed)
        updates_layout.addRow(self.update_auto)
        updates_layout.addRow(self.version_label)
        update_actions = QHBoxLayout()
        update_actions.setContentsMargins(0, 0, 0, 0)
        update_actions.addWidget(self.check_updates_button)
        update_actions.addWidget(self.update_status, 1)
        updates_layout.addRow(update_actions)

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

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(policy_group)
        layout.addWidget(routing_group)
        layout.addWidget(appearance_group)
        layout.addWidget(updates_group)
        layout.addWidget(ai_group)
        layout.addWidget(storage_group)
        layout.addWidget(save_button)
        layout.addWidget(
            _note(
                f"{config.APP_NAME} {config.APP_VERSION} - AI is optional and never authoritative. Core "
                "features (inventory, SSH/SFTP/serial, diagnostics, memory, audit) work with no network "
                "and no model."
            )
        )
        layout.addStretch(1)

        content = QWidget()
        content.setLayout(layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.refresh()

    # -- loading/saving -----------------------------------------------------
    def refresh(self) -> None:
        self.allow_destructive.setChecked(self.setting("policy.allow_destructive", "0") == "1")
        self.confirm_state_changes.setChecked(self.setting("policy.confirm_state_changes", "1") == "1")
        self.allow_intrusive.setChecked(self.setting("policy.allow_intrusive", "0") == "1")
        self.relay_edit.setText(self.setting("relay.allowed_destinations", "") or "")
        theme = self.setting("ui.theme", current_theme()) or current_theme()
        index = self.theme_combo.findData(theme)
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(index if index >= 0 else 0)
        self.theme_combo.blockSignals(False)
        self.update_feed.setText(self.services.update_feed_url)
        self.update_auto.setChecked(self.services.update_auto_check)
        self.version_label.setText(f"Installed version: {config.APP_VERSION}")
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

        theme = self.theme_combo.currentData() or current_theme()
        database.set_setting("ui.theme", str(theme))
        if self._on_theme_changed is not None:
            self._on_theme_changed(str(theme))
        self.services.save_update_feed_url(self.update_feed.text())
        self.services.save_update_auto_check(self.update_auto.isChecked())

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
                "theme": str(theme),
                "update_auto_check": self.update_auto.isChecked(),
            },
        )
        QMessageBox.information(self, "Settings saved", "Settings were updated.")

    # -- appearance ---------------------------------------------------------
    def _theme_selected(self, _index: int) -> None:
        theme = self.theme_combo.currentData()
        if theme and self._on_theme_changed is not None:
            self._on_theme_changed(str(theme))

    # -- updates ------------------------------------------------------------
    def check_for_updates(self) -> None:
        url = self.update_feed.text().strip()
        if not url:
            QMessageBox.information(
                self,
                "No update feed",
                "Set the HTTPS URL of the update manifest first.\n\n"
                "The feed is optional: Nexus Remote never contacts a server unless one is configured.",
            )
            return
        self.services.save_update_feed_url(url)
        self.services.save_update_auto_check(self.update_auto.isChecked())
        self.check_updates_button.setEnabled(False)
        self.update_status.setText("Checking the update feed\u2026")
        checker = UpdateChecker(url)
        run_async(
            checker.check,
            on_result=lambda result: self._update_checked(checker, result),
            on_error=self._update_failed,
        )

    def _update_checked(self, checker: UpdateChecker, result: UpdateCheckResult) -> None:
        self.check_updates_button.setEnabled(True)
        self.update_status.setText(result.message)
        self._audit_update(
            "update.checked",
            outcome="success" if result.status != "error" else "failure",
            severity="info" if result.status != "error" else "warning",
            detail={"status": result.status, "version": result.info.version if result.info else ""},
        )
        if result.status == "error":
            QMessageBox.warning(self, "Update check failed", result.message)
            return
        if not result.available:
            QMessageBox.information(self, "No update available", result.message)
            return
        info = result.info
        assert info is not None
        notes = f"\n\n{info.notes}" if info.notes else ""
        answer = QMessageBox.question(
            self,
            "Update available",
            f"{result.message}{notes}\n\nDownload the verified installer and start it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.check_updates_button.setEnabled(False)
        self.update_status.setText(f"Downloading {info.version}\u2026")
        destination = self.services.paths.root / "updates"
        run_async(
            checker.download,
            info,
            destination,
            on_result=self._update_downloaded,
            on_error=self._update_failed,
        )

    def _update_downloaded(self, result) -> None:
        self.check_updates_button.setEnabled(True)
        if not getattr(result, "ok", False):
            self.update_status.setText(getattr(result, "message", "The update failed."))
            self._audit_update(
                "update.download_failed",
                outcome="failure",
                severity="warning",
                detail={"message": self.update_status.text()[:300]},
            )
            QMessageBox.warning(self, "Update failed", self.update_status.text())
            return
        self.update_status.setText(result.message)
        self._audit_update(
            "update.downloaded",
            outcome="success",
            severity="notice",
            detail={"version": result.info.version if result.info else ""},
        )
        if not launch_installer(result.path):
            QMessageBox.warning(
                self, "Could not start the installer", f"The installer is at:\n{result.path}"
            )
            return
        self._audit_update(
            "update.launch",
            outcome="success",
            severity="notice",
            detail={"version": result.info.version if result.info else ""},
        )
        QMessageBox.information(
            self,
            "Installer started",
            "The setup wizard is now open. Nexus Remote will close so the update can replace its files.",
        )
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def _update_failed(self, message: str, _detail: str) -> None:
        self.check_updates_button.setEnabled(True)
        self.update_status.setText(message)
        self._audit_update(
            "update.check_failed",
            outcome="failure",
            severity="warning",
            detail={"message": message[:300]},
        )
        QMessageBox.warning(self, "Update failed", message)

    def _audit_update(self, action: str, *, outcome: str, severity: str, detail: dict) -> None:
        self.services.audit.record(
            "system", action, severity=severity, outcome=outcome, detail=detail
        )


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Faint")
    label.setWordWrap(True)
    return label
