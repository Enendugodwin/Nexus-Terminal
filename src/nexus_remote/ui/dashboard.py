"""Dashboard: inventory and session health at a glance."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.connections.interfaces import SessionState
from nexus_remote.services import AppServices
from nexus_remote.ui.theme import TOKENS


class MetricCard(QWidget):
    def __init__(self, label: str, value: str = "-", hint: str = "") -> None:
        super().__init__()
        self.setObjectName("Card")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("MetricValue")
        self.label_label = QLabel(label)
        self.label_label.setObjectName("MetricLabel")
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("Faint")
        self.hint_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

    def set_value(self, value: str, hint: str = "") -> None:
        self.value_label.setText(value)
        self.hint_label.setText(hint)


class DashboardPage(QWidget):
    """Summary of the inventory, sessions and security-relevant state."""

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services

        self.devices_card = MetricCard("Devices", "0", "saved device profiles")
        self.sessions_card = MetricCard("Active sessions", "0", "connected right now")
        self.credentials_card = MetricCard("Stored credentials", "0", "references in the OS vault")
        self.audit_card = MetricCard("Audit events", "0", "security-relevant records")
        self.pending_keys_card = MetricCard("Host keys pending", "0", "awaiting your decision")

        cards = QGridLayout()
        cards.setSpacing(12)
        for column, card in enumerate(
            (
                self.devices_card,
                self.sessions_card,
                self.credentials_card,
                self.audit_card,
                self.pending_keys_card,
            )
        ):
            cards.addWidget(card, 0, column)

        self.environment_label = QLabel("")
        self.environment_label.setObjectName("Muted")
        self.environment_label.setWordWrap(True)

        self.recent_table = QTableWidget(0, 5)
        self.recent_table.setHorizontalHeaderLabels(["Device", "Host", "Environment", "Protocols", "Last connection"])
        self.recent_table.verticalHeader().setVisible(False)
        self.recent_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recent_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recent_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.recent_table.setAlternatingRowColors(True)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)

        header = QHBoxLayout()
        title = QLabel("Recent devices")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.refresh_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addLayout(cards)
        layout.addWidget(self.environment_label)
        layout.addLayout(header)
        layout.addWidget(self.recent_table, 1)
        layout.addWidget(
            _hint(
                "Nexus Remote works without AI and without internet access: device inventory, diagnostics, "
                "command memory and the SSH/SFTP stack are all local."
            )
        )

    def refresh(self) -> None:
        devices = self.services.devices.list(sort="updated")
        environments = self.services.devices.count_by_environment()
        self.devices_card.set_value(str(len(devices)), f"{len(environments)} environment(s)")
        self.sessions_card.set_value(str(self.services.sessions.count_active()), "connected right now")
        self.credentials_card.set_value(str(len(self.services.credentials.references())), "references in the OS vault")
        self.audit_card.set_value(str(self.services.audit.count()), "security-relevant records")
        pending = len(self.services.host_keys.pending())
        self.pending_keys_card.set_value(str(pending), "review in the Security Center")
        self.pending_keys_card.value_label.setStyleSheet(
            f"color: {TOKENS['warn']};" if pending else ""
        )

        breakdown = ", ".join(f"{name}: {count}" for name, count in environments.items()) or "no devices yet"
        self.environment_label.setText(f"Environments - {breakdown}")

        rows = devices[:25]
        self.recent_table.setRowCount(len(rows))
        for row, device in enumerate(rows):
            protocols = ", ".join(device.enabled_protocols()) or "none"
            last = device.last_connected_at or "never"
            if device.last_connection_ok is False:
                last += "  (failed)"
            values = [device.name, device.hostname, device.environment, protocols, last]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, device.id)
                self.recent_table.setItem(row, column, item)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Faint")
    label.setWordWrap(True)
    return label
