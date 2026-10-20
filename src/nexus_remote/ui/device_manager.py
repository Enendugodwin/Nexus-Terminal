"""Device inventory page: search, filter, group, import/export, connect."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.devices.models import ValidationError
from nexus_remote.devices.profiles import ProfileError, export_devices, import_profiles
from nexus_remote.devices.repository import DuplicateDeviceError
from nexus_remote.services import AppServices
from nexus_remote.ui.device_dialog import DeviceDialog

COLUMNS = ["Name", "Host", "OS", "Environment", "Criticality", "Protocols", "Group", "Last connection", "Favourite"]


class DeviceManagerPage(QWidget):
    """The central device inventory."""

    connect_requested = Signal(str, str)  # device id, protocol

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self._devices = []

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search devices, tags, owners…")
        self.search_edit.textChanged.connect(self.refresh)

        self.group_filter = QComboBox()
        self.group_filter.currentIndexChanged.connect(self.refresh)
        self.env_filter = QComboBox()
        self.env_filter.currentIndexChanged.connect(self.refresh)
        self.protocol_filter = QComboBox()
        for protocol in ("any", "ssh", "sftp", "rdp", "smb", "serial"):
            self.protocol_filter.addItem(protocol, protocol)
        self.protocol_filter.currentIndexChanged.connect(self.refresh)

        self.add_button = QPushButton("Add device")
        self.add_button.setObjectName("Primary")
        self.add_button.clicked.connect(self.add_device)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(self.delete_selected)
        self.import_button = QPushButton("Import…")
        self.import_button.clicked.connect(self.import_profiles)
        self.export_button = QPushButton("Export…")
        self.export_button.clicked.connect(self.export_profiles)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("Primary")
        self.connect_button.clicked.connect(self.connect_selected)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.search_edit, 2)
        toolbar.addWidget(self.group_filter, 1)
        toolbar.addWidget(self.env_filter, 1)
        toolbar.addWidget(self.protocol_filter, 1)
        toolbar.addWidget(self.connect_button)
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addWidget(self.import_button)
        toolbar.addWidget(self.export_button)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.doubleClicked.connect(lambda _index: self.connect_selected())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Faint")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addLayout(toolbar)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status_label)

        self.refresh()

    # -- data ---------------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_filters()
        group = self.group_filter.currentData()
        protocol = self.protocol_filter.currentData()
        protocol = None if protocol in (None, "any") else protocol
        environment = self.env_filter.currentData()
        environment = None if environment in (None, "any") else environment
        self._devices = self.services.devices.list(
            self.search_edit.text(),
            group=group or None,
            protocol=protocol,
            environment=environment,
            sort="name",
        )
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._devices))
        for row, device in enumerate(self._devices):
            last = device.last_connected_at or "never"
            if device.last_connection_ok is False:
                last = f"{last} (failed)"
            values = [
                device.name,
                device.hostname,
                device.operating_system,
                device.environment,
                device.criticality,
                ", ".join(device.enabled_protocols()) or "-",
                device.group_name or "-",
                last,
                "yes" if device.favorite else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, device.id)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        total = self.services.devices.count()
        self.status_label.setText(
            f"Showing {len(self._devices)} of {total} device(s). "
            "Profiles never contain credentials - secrets live in the OS vault."
        )

    def _refresh_filters(self) -> None:
        current_group = self.group_filter.currentData()
        self.group_filter.blockSignals(True)
        self.group_filter.clear()
        self.group_filter.addItem("all groups", None)
        for group in self.services.devices.list_groups():
            self.group_filter.addItem(group.name, group.name)
        index = self.group_filter.findData(current_group)
        self.group_filter.setCurrentIndex(max(0, index))
        self.group_filter.blockSignals(False)

        current_env = self.env_filter.currentData()
        self.env_filter.blockSignals(True)
        self.env_filter.clear()
        self.env_filter.addItem("all environments", None)
        for environment in self.services.devices.count_by_environment():
            self.env_filter.addItem(environment, environment)
        index = self.env_filter.findData(current_env)
        self.env_filter.setCurrentIndex(max(0, index))
        self.env_filter.blockSignals(False)

    def selected_device(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        if item is None:
            return None
        return self.services.devices.get(item.data(Qt.ItemDataRole.UserRole))

    def select_device(self, device_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == device_id:
                self.table.selectRow(row)
                return

    # -- actions ------------------------------------------------------------
    def add_device(self) -> None:
        dialog = DeviceDialog(self.services, None, self)
        if dialog.exec() != DeviceDialog.DialogCode.Accepted:
            return
        try:
            device = dialog.device()
            group_name = dialog.selected_group()
            self.services.devices.add(device)
            if group_name:
                group = self.services.devices.ensure_group(group_name)
                self.services.devices.assign_group(device.id, group.id)
            self.services.audit.record(
                "device",
                "device.created",
                outcome="success",
                device_id=device.id,
                subject=f"{device.name} ({device.hostname})",
            )
            self.refresh()
            self.select_device(device.id)
            answer = QMessageBox.question(
                self,
                "Store a credential?",
                f"Store a credential for {device.name} in the Windows Credential Manager now?",
            )
            if answer == QMessageBox.StandardButton.Yes:  # pragma: no cover - needs user input
                from nexus_remote.ui.device_dialog import CredentialPromptDialog

                CredentialPromptDialog(self.services, device, self).exec()
        except DuplicateDeviceError as exc:
            if QMessageBox.question(self, "Duplicate device", f"{exc}\n\nAdd it anyway?") == QMessageBox.StandardButton.Yes:
                self.services.devices.add(dialog.device(), allow_duplicate=True)
                self.refresh()
        except ValidationError as exc:
            QMessageBox.warning(self, "Invalid device", str(exc))

    def edit_selected(self) -> None:
        device = self.selected_device()
        if device is None:
            QMessageBox.information(self, "No selection", "Select a device to edit.")
            return
        dialog = DeviceDialog(self.services, device, self)
        if dialog.exec() != DeviceDialog.DialogCode.Accepted:
            return
        try:
            updated = dialog.device()
            self.services.devices.update(updated)
            group_name = dialog.selected_group()
            if group_name:
                group = self.services.devices.ensure_group(group_name)
                self.services.devices.assign_group(updated.id, group.id)
            else:
                self.services.devices.assign_group(updated.id, None)
            self.services.audit.record(
                "device", "device.updated", outcome="success", device_id=updated.id, subject=updated.name
            )
            self.refresh()
        except ValidationError as exc:
            QMessageBox.warning(self, "Invalid device", str(exc))

    def delete_selected(self) -> None:
        device = self.selected_device()
        if device is None:
            QMessageBox.information(self, "No selection", "Select a device to delete.")
            return
        references = self.services.credentials.references(device.id)
        detail = (
            f"\n\n{len(references)} stored credential reference(s) will also be removed from the vault."
            if references
            else ""
        )
        if (
            QMessageBox.question(
                self, "Delete device", f"Delete {device.name} ({device.hostname})?{detail}"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.services.credentials.revoke_device(device.id)
        self.services.devices.delete(device.id)
        self.services.audit.record(
            "device",
            "device.deleted",
            severity="notice",
            outcome="success",
            subject=device.name,
            detail={"credentials_revoked": len(references)},
        )
        self.refresh()

    def connect_selected(self) -> None:
        device = self.selected_device()
        if device is None:
            QMessageBox.information(self, "No selection", "Select a device to connect to.")
            return
        protocols = device.enabled_protocols()
        if not protocols:
            QMessageBox.warning(self, "No protocol", f"{device.name} has no enabled protocol.")
            return
        if len(protocols) == 1:
            self.connect_requested.emit(device.id, protocols[0])
            return
        protocol, ok = QInputDialog.getItem(
            self, "Connect", f"Protocol for {device.name}:", protocols, editable=False
        )
        if ok:
            self.connect_requested.emit(device.id, protocol)

    def import_profiles(self) -> None:  # pragma: no cover - needs a file dialog
        path, _ = QFileDialog.getOpenFileName(self, "Import device profiles", "", "YAML (*.yaml *.yml);;All files (*)")
        if not path:
            return
        try:
            mode, ok = QInputDialog.getItem(
                self,
                "Import mode",
                "How should conflicting profiles be handled?",
                ["skip", "rename", "overwrite"],
                editable=False,
            )
            if not ok:
                return
            result = import_profiles(path, self.services.devices, mode=mode)
            self.services.audit.record(
                "device", "devices.imported", outcome="success", subject=Path(path).name, detail=result
            )
            self.refresh()
            QMessageBox.information(
                self,
                "Import complete",
                f"Added: {len(result['added'])}, renamed: {len(result['renamed'])}, "
                f"overwritten: {len(result['overwritten'])}, skipped: {len(result['skipped'])}.",
            )
        except ProfileError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))

    def export_profiles(self) -> None:  # pragma: no cover - needs a file dialog
        if not self._devices:
            QMessageBox.information(self, "Nothing to export", "There are no devices to export.")
            return
        default = str(Path(self.services.paths.exports) / "nexus-devices.yaml")
        path, _ = QFileDialog.getSaveFileName(self, "Export device profiles", default, "YAML (*.yaml *.yml)")
        if not path:
            return
        try:
            target = export_devices(self._devices, path)
            self.services.audit.record(
                "device", "devices.exported", outcome="success", subject=Path(target).name
            )
            QMessageBox.information(
                self,
                "Export complete",
                f"Wrote {len(self._devices)} profile(s) to:\n{target}\n\nSecrets are never exported.",
            )
        except ProfileError as exc:
            QMessageBox.warning(self, "Export refused", str(exc))

    def _context_menu(self, position) -> None:  # pragma: no cover - interactive
        device = self.selected_device()
        if device is None:
            return
        menu = QMenu(self)
        for protocol in device.enabled_protocols():
            action = QAction(f"Connect via {protocol.upper()}", self)
            action.triggered.connect(lambda _checked=False, proc=protocol, dev=device: self.connect_requested.emit(dev.id, proc))
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Edit", self.edit_selected)
        menu.addAction("Delete", self.delete_selected)
        menu.exec(self.table.viewport().mapToGlobal(position))
