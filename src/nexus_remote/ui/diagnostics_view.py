"""Diagnostics page: evidence-based connectivity checks."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from nexus_remote.diagnostics.reports import PROTOCOL_PORTS
from nexus_remote.services import AppServices
from nexus_remote.ui.tasks import Worker, connect_worker
from nexus_remote.ui.theme import TOKENS


def _result_colours() -> dict[str, str]:
    return {
        "ok": TOKENS["ok"],
        "failed": TOKENS["danger"],
        "partial": TOKENS["warn"],
        "skipped": TOKENS["text_muted"],
    }


class DiagnosticsPage(QWidget):
    """Run DNS, route, TCP and protocol checks and read the interpretation."""

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services

        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("hostname or IP address")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setValue(22)
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItem("custom port", None)
        for protocol, port in PROTOCOL_PORTS.items():
            self.protocol_combo.addItem(f"{protocol} ({port})", protocol)
        self.protocol_combo.currentIndexChanged.connect(self._on_protocol_changed)
        self.ping_check = QCheckBox("Include ICMP ping")
        self.reverse_check = QCheckBox("Include reverse DNS")
        self.run_button = QPushButton("Run checks")
        self.run_button.setObjectName("Primary")
        self.run_button.clicked.connect(self.run_checks)
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(220)
        self.preset_combo.addItem("device…", None)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        self.results_table = QTableWidget(0, 5)
        self.results_table.setHorizontalHeaderLabels(
            ["Test", "Destination", "Result", "What it shows", "Next step"]
        )
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setColumnWidth(0, 170)
        self.results_table.setColumnWidth(1, 190)
        self.results_table.setColumnWidth(2, 80)
        self.results_table.setAlternatingRowColors(True)

        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setObjectName("Mono")
        copy_button = QPushButton("Copy report")
        copy_button.clicked.connect(self._copy_report)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Target"))
        controls.addWidget(self.target_edit, 2)
        controls.addWidget(QLabel("Protocol"))
        controls.addWidget(self.protocol_combo, 1)
        controls.addWidget(QLabel("Port"))
        controls.addWidget(self.port_spin)
        controls.addWidget(self.ping_check)
        controls.addWidget(self.reverse_check)
        controls.addWidget(self.run_button)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Fill from device"))
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addStretch(2)

        splitter = QSplitter(Qt.Orientation.Vertical)
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.results_table)
        report_container = QWidget()
        report_layout = QVBoxLayout(report_container)
        report_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("Structured report"))
        header.addStretch(1)
        header.addWidget(copy_button)
        report_layout.addLayout(header)
        report_layout.addWidget(self.report_view)
        splitter.addWidget(table_container)
        splitter.addWidget(report_container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addLayout(controls)
        layout.addLayout(preset_row)
        layout.addWidget(splitter, 1)
        note = QLabel(
            "Interpretations state only what each test establishes. A TCP timeout cannot prove which "
            "firewall, router or ACL dropped the packet, and a failed ping never proves a device is offline."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.refresh_devices()
        self._last_report_text = ""

    # -- data ---------------------------------------------------------------
    def refresh_devices(self) -> None:
        current = self.preset_combo.currentData()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("device…", None)
        for device in self.services.devices.list(sort="name"):
            protocol = device.primary_protocol()
            label = f"{device.name} ({device.hostname}:{device.connection(protocol).port if protocol else '-'})"
            self.preset_combo.addItem(label, device.id)
        index = self.preset_combo.findData(current)
        self.preset_combo.setCurrentIndex(max(0, index))
        self.preset_combo.blockSignals(False)

    def _on_preset_changed(self) -> None:
        device_id = self.preset_combo.currentData()
        if not device_id:
            return
        device = self.services.devices.get(device_id)
        if device is None:
            return
        protocol = device.primary_protocol()
        self.set_target(device.hostname, device.connection(protocol).port if protocol else None, protocol or "")

    def _on_protocol_changed(self) -> None:
        protocol = self.protocol_combo.currentData()
        if protocol:
            self.port_spin.setValue(PROTOCOL_PORTS.get(protocol, self.port_spin.value()))

    def set_target(self, host: str, port: int | None = None, protocol: str = "") -> None:
        """Prefill the form (used when the workspace asks for a diagnostic run)."""
        self.target_edit.setText(host)
        if port:
            self.port_spin.setValue(int(port))
        index = self.protocol_combo.findData(protocol) if protocol else 0
        self.protocol_combo.setCurrentIndex(max(0, index))

    # -- actions ------------------------------------------------------------
    def run_checks(self) -> None:  # pragma: no cover - exercises the network
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.information(self, "No target", "Enter a hostname or IP address.")
            return
        port = self.port_spin.value() or None
        protocol = self.protocol_combo.currentData()
        self.run_button.setEnabled(False)
        self.report_view.setPlainText("running checks…")

        def work():
            return self.services.diagnostics.run(
                target,
                port,
                protocol=protocol,
                include_ping=self.ping_check.isChecked(),
                include_reverse=self.reverse_check.isChecked(),
            )

        def finished(report) -> None:
            self.run_button.setEnabled(True)
            self._render(report)

        def failed(message: str, detail: str) -> None:
            self.run_button.setEnabled(True)
            self.report_view.setPlainText(f"diagnostics failed: {message}\n{detail}")

        worker = Worker(work)
        connect_worker(worker, on_result=finished, on_error=failed)
        QThreadPool.globalInstance().start(worker)

    def _render(self, report) -> None:
        rows = report.as_rows()
        self.results_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, key in enumerate(("test", "destination", "result", "interpretation", "next_step")):
                item = QTableWidgetItem(str(values.get(key, "")))
                if key == "result":
                    colour = _result_colours().get(str(values.get(key)), "")
                    if colour:
                        item.setForeground(_colour(colour))
                self.results_table.setItem(row, column, item)
        self.results_table.resizeRowsToContents()
        self._last_report_text = report.render_text()
        self.report_view.setPlainText(self._last_report_text)
        self.services.audit.record(
            "system",
            "diagnostics.run",
            outcome="success" if report.ok else "failure",
            subject=f"{report.target}:{report.port or '-'}",
            detail={"summary": report.summary, "results": [row["result"] for row in rows]},
        )

    def _copy_report(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._last_report_text)
        self.report_view.appendPlainText("\n[report copied to clipboard]")


def _colour(value: str):
    from PySide6.QtGui import QColor

    return QColor(value)
