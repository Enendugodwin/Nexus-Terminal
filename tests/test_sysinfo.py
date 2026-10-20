"""Remote system information: probe parsing, formatting and the panel."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from nexus_remote.diagnostics.sysinfo import (
    SYSINFO_COMMAND,
    SystemInfo,
    SystemInfoUnavailable,
    collect_system_info,
    format_bytes,
    format_load,
    format_percent,
    format_uptime,
    parse_system_info,
)

LINUX_OUTPUT = """@@host
web-01
@@uname
Linux 6.8.0-45-generic x86_64
@@os
NAME="Ubuntu"
VERSION="24.04.1 LTS (Noble Numbat)"
ID=ubuntu
VERSION_ID="24.04"
PRETTY_NAME="Ubuntu 24.04.1 LTS"
@@cpu
model name\t: Intel(R) Xeon(R) CPU E5-2670 0 @ 2.60GHz
8
@@mem
MemTotal:        8123456 kB
MemFree:          512345 kB
MemAvailable:    3212345 kB
Buffers:          123456 kB
Cached:          2345678 kB
SwapTotal:       2097148 kB
SwapFree:        1572864 kB
@@disk
Filesystem     1024-blocks      Used Available Capacity Mounted on
/dev/sda1         61202244  24153012  33904232      42% /
@@uptime
427404.52 1587654.12
@@load
0.42 0.31 0.28 2/345 6789
@@users
2
@@end
"""


def test_parse_reads_every_collected_value():
    info = parse_system_info(LINUX_OUTPUT)

    assert info.available
    assert info.hostname == "web-01"
    assert info.os_name == "Ubuntu 24.04.1 LTS"
    assert info.kernel == "Linux"
    assert info.architecture == "x86_64"
    assert info.cpu_model.startswith("Intel(R) Xeon(R)")
    assert info.cpu_cores == 8
    assert info.memory_total == 8123456 * 1024
    assert info.memory_used == (8123456 - 3212345) * 1024
    assert info.swap_total == 2097148 * 1024
    assert info.swap_used == (2097148 - 1572864) * 1024
    assert info.disk_total == 61202244 * 1024
    assert info.disk_used == 24153012 * 1024
    assert info.disk_mount == "/"
    assert info.uptime_seconds == pytest.approx(427404.52)
    assert (info.load_1, info.load_5, info.load_15) == (0.42, 0.31, 0.28)
    assert info.logged_in_users == 2
    assert info.memory_percent == pytest.approx(60.46, abs=0.01)
    assert info.disk_percent == pytest.approx(39.47, abs=0.01)


def test_memory_falls_back_when_mem_available_is_missing():
    text = """@@host
old-box
@@mem
MemTotal: 1000 kB
MemFree: 200 kB
Buffers: 100 kB
Cached: 300 kB
SwapTotal: 0 kB
SwapFree: 0 kB
@@end
"""
    info = parse_system_info(text)

    assert info.memory_total == 1000 * 1024
    assert info.memory_used == (1000 - 600) * 1024
    assert info.swap_total == 0
    assert info.swap_used == 0


def test_os_release_falls_back_to_name_and_version():
    text = """@@host
srv
@@os
NAME="Alpine Linux"
VERSION_ID=3.20
@@end
"""
    assert parse_system_info(text).os_name == "Alpine Linux 3.20"


def test_empty_or_foreign_output_is_reported_as_unavailable():
    with pytest.raises(SystemInfoUnavailable):
        parse_system_info("")
    with pytest.raises(SystemInfoUnavailable):
        parse_system_info("Microsoft Windows [Version 10.0.22631.4169]\r\nC:\\Users\\deploy>")


def test_partial_output_keeps_what_was_reported():
    text = """@@host
tiny
@@disk
Filesystem 1024-blocks Used Available Capacity Mounted on
overlay 2097152 1048576 1048576 50% /
@@end
"""
    info = parse_system_info(text)

    assert info.hostname == "tiny"
    assert info.disk_total == 2097152 * 1024
    assert info.memory_total is None
    assert info.cpu_cores is None


def test_collect_uses_the_fixed_probe_command():
    class FakeAdapter:
        def __init__(self) -> None:
            self.calls: list[tuple[str, float]] = []

        def execute(self, command: str, timeout: float = 30.0):
            self.calls.append((command, timeout))
            return SimpleNamespace(stdout=LINUX_OUTPUT, exit_code=0)

    adapter = FakeAdapter()
    info = collect_system_info(adapter, timeout=5.0)

    assert adapter.calls == [(SYSINFO_COMMAND, 5.0)]
    assert info.hostname == "web-01"


def test_collect_rejects_adapters_without_execute():
    with pytest.raises(SystemInfoUnavailable):
        collect_system_info(object())


def test_formatting_helpers():
    assert format_bytes(None) == "-"
    assert format_bytes(512) == "512 B"
    assert format_bytes(8 * 1024**3) == "8.0 GB"
    assert format_bytes(1536 * 1024**2) == "1.5 GB"
    assert format_uptime(None) == "-"
    assert format_uptime(59) == "0m"
    assert format_uptime(3660) == "1h 1m"
    assert format_uptime(90061) == "1d 1h"
    assert format_percent(None) == "-"
    assert format_percent(39.47) == "39%"
    info = SystemInfo(load_1=0.5, load_5=1.25, load_15=2.0)
    assert format_load(info) == "0.50 / 1.25 / 2.00"
    assert format_load(SystemInfo()) == "-"


# -- UI ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_panel_starts_idle_and_locked(app):
    from nexus_remote.ui.system_info import SystemInfoPanel

    panel = SystemInfoPanel()

    assert panel.refresh_button.isEnabled() is False
    assert "Connect to an SSH device" in panel.status_label.text()
    assert panel.cpu_card.value_label.text() == "-"
    panel.deleteLater()


def test_panel_renders_cpu_memory_storage_os_and_uptime(app):
    from nexus_remote.ui.system_info import SystemInfoPanel

    panel = SystemInfoPanel()
    panel.show_info(parse_system_info(LINUX_OUTPUT), "web-01")

    assert panel.cpu_card.value_label.text() == "8 cores"
    assert "Intel(R) Xeon(R)" in panel.cpu_card.hint_label.text()
    assert panel.memory_card.value_label.text() == "4.7 GB / 7.7 GB"
    assert "60% used" in panel.memory_card.hint_label.text()
    assert "swap" in panel.memory_card.hint_label.text()
    assert panel.storage_card.value_label.text() == "23.0 GB / 58.4 GB"
    assert "39% used" in panel.storage_card.hint_label.text()
    assert panel.system_card.value_label.text() == "Ubuntu 24.04.1 LTS"
    assert "Linux" in panel.system_card.hint_label.text()
    assert panel.uptime_card.value_label.text() == "4d 22h"
    assert "load 0.42 / 0.31 / 0.28" in panel.uptime_card.hint_label.text()
    assert "2 logged in" in panel.uptime_card.hint_label.text()
    assert panel.refresh_button.isEnabled() is True
    assert panel.status_label.text().startswith("Updated ")
    panel.deleteLater()


def test_panel_reports_unavailable_hosts_without_clearing_controls(app):
    from nexus_remote.ui.system_info import SystemInfoPanel

    panel = SystemInfoPanel()
    panel.show_error("the host did not return POSIX system information")

    assert "unavailable" in panel.status_label.text()
    assert "POSIX" in panel.status_label.text()
    assert panel.refresh_button.isEnabled() is True
    panel.deleteLater()


def test_workspace_places_system_info_under_the_terminal(app, services):
    from nexus_remote.ui.workspace import WorkspacePage

    workspace = WorkspacePage(services)
    layout = workspace.terminal_view.parentWidget().layout()

    assert layout.indexOf(workspace.terminal_view) < layout.indexOf(workspace.system_info)
    assert layout.indexOf(workspace.system_info) < layout.indexOf(workspace.terminal_status)
    assert workspace.system_info.refresh_button.isEnabled() is False
    assert workspace._refresh_system_info() is None


def test_workspace_updates_the_strip_from_a_collected_snapshot(app, services, device):
    from nexus_remote.ui.workspace import WorkspacePage

    workspace = WorkspacePage(services)
    session = services.sessions.open(device, "ssh")
    workspace.terminal_view.session_id = session.id

    workspace._on_system_info(session, parse_system_info(LINUX_OUTPUT))

    assert workspace.system_info.cpu_card.value_label.text() == "8 cores"
    assert workspace.system_info.storage_card.value_label.text() == "23.0 GB / 58.4 GB"
    assert workspace.system_info.refresh_button.isEnabled() is True
