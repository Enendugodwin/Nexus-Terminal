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
from nexus_remote.assistant.providers import (
    AIConfig,
    PROVIDERS,
    build_model,
    preset_for,
    validate_endpoint,
)
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

        self._ai_loading = False
        self.ai_provider = QComboBox()
        for preset in PROVIDERS:
            self.ai_provider.addItem(preset.label, preset.key)
        self.ai_provider.currentIndexChanged.connect(self._ai_provider_changed)
        self.ai_endpoint = QLineEdit()
        self.ai_endpoint.setPlaceholderText("https://api.example.com/v1/chat/completions")
        self.ai_model = QLineEdit()
        self.ai_api_key = QLineEdit()
        self.ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_api_key.setPlaceholderText("paste a key to store it in the OS vault (never SQLite)")
        self.ai_key_status = QLabel("")
        self.ai_key_status.setObjectName("Faint")
        self.ai_key_status.setWordWrap(True)
        self.ai_clear_key = QCheckBox("Remove the stored key for this provider")
        self.ai_test_button = QPushButton("Test provider")
        self.ai_test_button.clicked.connect(self._test_ai_provider)
        self.ai_test_status = QLabel("")
        self.ai_test_status.setObjectName("Faint")
        self.ai_test_status.setWordWrap(True)
        self.ai_provider_note = QLabel("")
        self.ai_provider_note.setObjectName("Faint")
        self.ai_provider_note.setWordWrap(True)
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
        ai_layout.addRow("Provider", self.ai_provider)
        ai_layout.addRow(self.ai_provider_note)
        ai_layout.addRow("Endpoint", self.ai_endpoint)
        ai_layout.addRow("Model", self.ai_model)
        ai_layout.addRow("API key", self.ai_api_key)
        ai_layout.addRow(self.ai_key_status)
        ai_layout.addRow(self.ai_clear_key)
        ai_test_row = QHBoxLayout()
        ai_test_row.setContentsMargins(0, 0, 0, 0)
        ai_test_row.addWidget(self.ai_test_button)
        ai_test_row.addWidget(self.ai_test_status, 1)
        ai_layout.addRow(ai_test_row)
        ai_layout.addRow(
            _note(
                "Hosted providers receive only what you send from the assistant. "
                "API keys are kept in the Windows Credential Manager, and a key is "
                "never sent over plain HTTP unless the endpoint is this computer (loopback)."
            )
        )

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
        ai = self.services.ai_config
        self._ai_loading = True
        try:
            index = self.ai_provider.findData(ai.provider)
            self.ai_provider.setCurrentIndex(index if index >= 0 else 0)
            self.ai_endpoint.setText(ai.endpoint)
            self.ai_model.setText(ai.model)
            self.ai_api_key.clear()
            self.ai_clear_key.setChecked(False)
        finally:
            self._ai_loading = False
        self._refresh_ai_provider_note()
        self._refresh_ai_key_state()
        self.paths_label.setText(
            f"Database: {self.services.paths.database}\nLogs: {self.services.paths.logs}\n"
            f"Exports: {self.services.paths.exports}"
        )

    def save(self) -> None:
        database = self.services.database
        if not self._save_ai_settings():
            return
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

        self.services.audit.record(
            "policy",
            "settings.saved",
            severity="notice",
            outcome="success",
            detail={
                "allow_destructive": self.allow_destructive.isChecked(),
                "allow_intrusive": self.allow_intrusive.isChecked(),
                "relay_destinations": destinations,
                "ai_provider": self.ai_provider.currentData(),
                "ai_enabled": self.ai_provider.currentData() != "disabled",
                "theme": str(theme),
                "update_auto_check": self.update_auto.isChecked(),
            },
        )
        QMessageBox.information(self, "Settings saved", "Settings were updated.")

    # -- optional AI --------------------------------------------------------
    def _ai_provider_changed(self, _index: int | None = None) -> None:
        if self._ai_loading:
            return
        preset = preset_for(str(self.ai_provider.currentData() or "disabled"))
        self.ai_endpoint.setText(preset.endpoint)
        self.ai_model.setText(preset.model)
        self.ai_api_key.clear()
        self.ai_clear_key.setChecked(False)
        self.ai_test_status.clear()
        self._refresh_ai_provider_note()
        self._refresh_ai_key_state()

    def _refresh_ai_provider_note(self) -> None:
        preset = preset_for(str(self.ai_provider.currentData() or "disabled"))
        self.ai_provider_note.setText(preset.note)
        enabled = preset.key != "disabled"
        for widget in (
            self.ai_endpoint,
            self.ai_model,
            self.ai_api_key,
            self.ai_clear_key,
            self.ai_test_button,
        ):
            widget.setEnabled(enabled)
        if not enabled:
            self.ai_key_status.setText("")
            self.ai_test_status.setText("")

    def _refresh_ai_key_state(self) -> None:
        preset = preset_for(str(self.ai_provider.currentData() or "disabled"))
        if preset.key == "disabled":
            self.ai_key_status.setText("")
            self.ai_clear_key.setEnabled(False)
            return
        stored = self.services.ai_key_configured(preset.key)
        if preset.requires_key:
            self.ai_key_status.setText(
                "A key is stored in the Windows Credential Manager."
                if stored
                else "No key is stored - this provider cannot be used until you save one."
            )
        else:
            self.ai_key_status.setText(
                "A key is stored in the Windows Credential Manager."
                if stored
                else "No API key is required; an optional key can still be stored."
            )
        self.ai_clear_key.setEnabled(stored)
        if not stored:
            self.ai_clear_key.setChecked(False)

    def _ai_config_from_form(self) -> tuple[AIConfig, str]:
        provider = str(self.ai_provider.currentData() or "disabled")
        preset = preset_for(provider)
        endpoint = self.ai_endpoint.text().strip()
        model = self.ai_model.text().strip()
        typed = self.ai_api_key.text().strip()
        clear = self.ai_clear_key.isChecked()
        if provider == "disabled":
            return AIConfig(provider="disabled"), ""
        if not model:
            raise ValueError("enter a model name")
        stored = "" if clear else self.services.ai_api_key(provider)
        key = typed or stored
        validate_endpoint(endpoint, has_secret=bool(key))
        # Removing a stored key is allowed; the provider then shows as "not
        # ready" until a new key is entered. It is not silently switched off.
        if preset.requires_key and not key and not clear:
            raise ValueError(f"{preset.label} requires an API key")
        return AIConfig(provider=provider, endpoint=endpoint, model=model), key

    def _save_ai_settings(self) -> bool:
        try:
            config, key = self._ai_config_from_form()
            if config.enabled and key:
                build_model(config, api_key=key)  # validate before persisting anything
        except ValueError as exc:
            QMessageBox.warning(self, "Check the AI settings", str(exc))
            return False

        typed = self.ai_api_key.text().strip()
        if typed:
            self.services.save_ai_api_key(config.provider, typed)
        elif self.ai_clear_key.isChecked():
            self.services.clear_ai_api_key(config.provider)
        database = self.services.database
        database.set_setting("ai.provider", config.provider)
        database.set_setting("ai.endpoint", config.endpoint)
        database.set_setting("ai.model", config.model)
        database.set_setting("ai.enabled", "1" if config.enabled else "0")
        self.services.configure_ai_from_settings()
        self.ai_api_key.clear()
        self.ai_clear_key.setChecked(False)
        self._refresh_ai_key_state()
        return True

    def _test_ai_provider(self) -> None:
        try:
            config, key = self._ai_config_from_form()
            if not config.enabled:
                self.ai_test_status.setText("Select a provider first.")
                return
            provider = build_model(config, api_key=key)
        except ValueError as exc:
            self.ai_test_status.setText(str(exc))
            return
        self.ai_test_button.setEnabled(False)
        self.ai_test_status.setText(f"Testing {config.label}...")
        run_async(
            provider.probe,
            on_result=lambda available: self._ai_tested(config.label, available),
            on_error=lambda message, _detail: self._ai_tested(config.label, False, message),
        )

    def _ai_tested(self, label: str, available: bool, message: str = "") -> None:
        self.ai_test_button.setEnabled(True)
        if available:
            self.ai_test_status.setText(
                f"{label}: reachable - the provider answered a test request."
            )
            return
        reason = (message or "").strip()
        self.ai_test_status.setText(
            f"{label}: failed{(' - ' + reason) if reason else ''}. "
            "Saved settings are unchanged; deterministic features remain available."
        )

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
