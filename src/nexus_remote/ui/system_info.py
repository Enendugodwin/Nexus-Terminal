"""System information strip shown under the interactive terminal.

Five compact cards summarise the connected host: CPU, memory, storage,
operating system and uptime (with load and logged-in users). The panel only
renders values - collection happens on a worker thread in the workspace.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.diagnostics.sysinfo import (
    SystemInfo,
    format_bytes,
    format_load,
    format_percent,
    format_uptime,
)

_IDLE = "Connect to an SSH device to read its CPU, memory and storage."


def _shorten(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


class SystemMetricCard(QWidget):
    """One labelled metric in the strip (title, value, detail)."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SysCard")
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SysTitle")
        self.value_label = QLabel("-")
        self.value_label.setObjectName("SysValue")
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("Faint")
        self.hint_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.hint_label)
        self.setMinimumWidth(130)

    def set_metric(self, value: str, hint: str = "", tooltip: str = "") -> None:
        self.value_label.setText(value or "-")
        self.hint_label.setText(hint)
        self.setToolTip(tooltip or "\n".join(part for part in (value, hint) if part))

    def set_unknown(self, hint: str = "") -> None:
        self.set_metric("-", hint)


class SystemInfoPanel(QWidget):
    """Read-only summary of the connected device, refreshed on demand."""

    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel("System information")
        title.setObjectName("CardTitle")
        self.status_label = QLabel(_IDLE)
        self.status_label.setObjectName("Faint")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Read CPU, memory, storage, OS and uptime from the connected host")
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.refresh_button.setEnabled(False)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(title)
        header.addWidget(self.status_label, 1)
        header.addWidget(self.refresh_button)

        self.cpu_card = SystemMetricCard("CPU")
        self.memory_card = SystemMetricCard("Memory")
        self.storage_card = SystemMetricCard("Storage")
        self.system_card = SystemMetricCard("System")
        self.uptime_card = SystemMetricCard("Uptime")

        cards = QHBoxLayout()
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(10)
        for card in (
            self.cpu_card,
            self.memory_card,
            self.storage_card,
            self.system_card,
            self.uptime_card,
        ):
            cards.addWidget(card, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addLayout(cards)
        self.reset()

    # -- states -------------------------------------------------------------
    def reset(self, message: str = _IDLE) -> None:
        for card in (
            self.cpu_card,
            self.memory_card,
            self.storage_card,
            self.system_card,
            self.uptime_card,
        ):
            card.set_unknown("not collected")
        self.status_label.setText(message)
        self.refresh_button.setEnabled(False)

    def set_loading(self, label: str = "") -> None:
        source = f" from {label}" if label else ""
        self.status_label.setText(f"Reading system information{source}…")
        self.refresh_button.setEnabled(False)

    def show_error(self, message: str) -> None:
        self.status_label.setText(f"System information unavailable: {message}")
        self.refresh_button.setEnabled(True)

    def show_info(self, info: SystemInfo, label: str = "") -> None:
        cpu_hint = _shorten(info.cpu_model, 42) or "model not reported"
        cpu_cores = f"{info.cpu_cores} core{'s' if info.cpu_cores != 1 else ''}" if info.cpu_cores else "-"
        self.cpu_card.set_metric(
            cpu_cores,
            cpu_hint,
            tooltip=f"{info.cpu_cores or 'Unknown'} cores\n{info.cpu_model or 'CPU model not reported'}",
        )

        memory_value = "-"
        memory_hint = "not reported"
        if info.memory_total:
            memory_value = f"{format_bytes(info.memory_used)} / {format_bytes(info.memory_total)}"
            memory_hint = f"{format_percent(info.memory_percent)} used"
            if info.swap_total:
                memory_hint += f" · swap {format_bytes(info.swap_used)} of {format_bytes(info.swap_total)}"
            else:
                memory_hint += " · no swap"
        self.memory_card.set_metric(memory_value, memory_hint, tooltip=memory_hint)

        storage_value = "-"
        storage_hint = "not reported"
        if info.disk_total:
            storage_value = f"{format_bytes(info.disk_used)} / {format_bytes(info.disk_total)}"
            storage_hint = f"{format_percent(info.disk_percent)} used · {info.disk_mount}"
        self.storage_card.set_metric(storage_value, storage_hint, tooltip=storage_hint)

        os_name = _shorten(info.os_name or info.hostname or info.kernel, 28) or "-"
        system_bits = " · ".join(
            part for part in (info.kernel, info.architecture, info.hostname) if part
        )
        self.system_card.set_metric(
            os_name,
            _shorten(system_bits, 46) or "not reported",
            tooltip=f"{info.os_name}\n{info.kernel} {info.architecture}\n{info.hostname}".strip(),
        )

        uptime_hint = f"load {format_load(info)}"
        if info.logged_in_users is not None:
            uptime_hint += f" · {info.logged_in_users} logged in"
        self.uptime_card.set_metric(format_uptime(info.uptime_seconds), uptime_hint, tooltip=uptime_hint)

        stamp = datetime.now().strftime("%H:%M")
        source = f" · {label}" if label else ""
        self.status_label.setText(f"Updated {stamp}{source}")
        self.refresh_button.setEnabled(True)
