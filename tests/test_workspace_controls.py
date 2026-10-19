"""Workspace controls: force-kill and the embedded FreeRDP desktop tab."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from nexus_remote.connections.interfaces import SessionState
from nexus_remote.devices.models import Device, ProtocolConfig
from nexus_remote.ui.workspace import WorkspacePage


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture()
def rdp_device():
    return Device(
        name="windows-lab",
        hostname="10.0.0.20",
        operating_system="windows",
        environment="lab",
        connections={
            "ssh": ProtocolConfig("ssh", enabled=True, port=22, username="deploy"),
            "rdp": ProtocolConfig("rdp", enabled=True, port=3389),
        },
    ).validate()


def test_sessions_tab_exposes_kill_controls(app, services):
    workspace = WorkspacePage(services)
    assert workspace.kill_all_button.text() == "Kill all sessions"
    assert workspace.force_button.text() == "Force kill"


def test_kill_all_sessions_closes_every_session(app, services, rdp_device):
    workspace = WorkspacePage(services)
    first = services.sessions.open(rdp_device, "ssh")
    second = services.sessions.open(rdp_device, "ssh")
    workspace._append_session(first)
    workspace._append_session(second)

    workspace.kill_all_sessions()

    assert first.state is SessionState.CLOSED
    assert second.state is SessionState.CLOSED
    assert services.sessions.count_active() == 0
    # Rows are refreshed in place, not appended again.
    assert workspace.session_table.rowCount() == 2


def test_force_kill_closes_the_selected_session(app, services, rdp_device):
    workspace = WorkspacePage(services)
    session = services.sessions.open(rdp_device, "ssh")
    workspace._append_session(session)
    workspace.session_table.selectRow(0)

    workspace.force_kill_selected()

    assert session.state is SessionState.CLOSED


def test_rdp_tab_is_an_embedded_native_window(app, services, rdp_device):
    services.devices.add(rdp_device)
    workspace = WorkspacePage(services)
    workspace.refresh_rdp_devices()

    assert workspace.rdp_embed.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    assert workspace.rdp_status.text() == "No embedded session."
    # No mstsc anywhere in the operator-facing copy.
    assert "mstsc" not in workspace.rdp_status.text().lower()


def test_assistant_panel_sits_beside_the_terminal(app, services):
    workspace = WorkspacePage(services)

    assert workspace.terminal_splitter.indexOf(workspace.assistant_panel) == 1
    assert workspace.assistant_panel.compact is True
    assert workspace.assistant_toggle.isChecked()
    assert workspace.assistant_panel.context_label.text() == "No terminal attached"


def test_assistant_can_be_hidden_and_shown_from_the_terminal(app, services):
    workspace = WorkspacePage(services)

    workspace.assistant_toggle.setChecked(False)
    assert workspace.assistant_panel.isHidden()

    workspace.assistant_toggle.setChecked(True)
    assert not workspace.assistant_panel.isHidden()


def test_assistant_follows_the_attached_shell_environment(app, services):
    workspace = WorkspacePage(services)

    workspace.assistant_panel.set_environment("powershell")
    assert workspace.assistant_panel.environment_combo.currentData() == "powershell"

    workspace.assistant_panel.set_context("Beside windows-lab - 10.0.0.20:22")
    assert "windows-lab" in workspace.assistant_panel.context_label.text()
