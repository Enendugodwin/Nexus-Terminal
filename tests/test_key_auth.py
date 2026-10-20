"""SSH private-key authentication: vault keys, key files and dialog wiring."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from nexus_remote.connections.interfaces import ConnectionErrorKind, ProtocolError


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _use_key_auth(device) -> None:
    device.connections["ssh"].auth_method = "ssh_key"


def _session(device):
    """A pending session without needing adapters (only _build_target is tested)."""
    from nexus_remote.connections.session import Session

    cfg = device.connections["ssh"]
    return Session(
        device_id=device.id,
        device_name=device.name,
        protocol="ssh",
        host=device.hostname,
        port=cfg.port,
        username=cfg.username,
    )


def test_target_uses_a_key_stored_in_the_vault(manager, stored_device, credentials):
    _use_key_auth(stored_device)
    username = stored_device.connections["ssh"].username
    credentials.save(stored_device.id, "ssh_private_key", username, "-----BEGIN KEY-----")
    credentials.save(stored_device.id, "ssh_key_passphrase", username, "unlock-me")

    session = _session(stored_device)
    target = manager._build_target(stored_device, session)

    assert target.private_key == "-----BEGIN KEY-----"
    assert target.passphrase == "unlock-me"
    assert target.secret is None


def test_target_uses_a_key_file_without_a_vault_entry(manager, stored_device):
    _use_key_auth(stored_device)
    stored_device.connections["ssh"].extra["key_filename"] = r"C:\keys\id_ed25519"

    session = _session(stored_device)
    target = manager._build_target(stored_device, session)

    assert target.private_key is None
    assert target.options["key_filename"] == r"C:\keys\id_ed25519"


def test_key_file_and_vault_key_can_be_combined(manager, stored_device, credentials):
    _use_key_auth(stored_device)
    stored_device.connections["ssh"].extra["key_filename"] = "/home/me/.ssh/id_ed25519"
    username = stored_device.connections["ssh"].username
    credentials.save(stored_device.id, "ssh_key_passphrase", username, "phrase")

    session = _session(stored_device)
    target = manager._build_target(stored_device, session)

    assert target.passphrase == "phrase"
    assert target.options["key_filename"].endswith("id_ed25519")


def test_missing_key_fails_with_a_helpful_auth_error(manager, stored_device):
    _use_key_auth(stored_device)
    session = _session(stored_device)

    with pytest.raises(ProtocolError) as excinfo:
        manager._build_target(stored_device, session)

    assert excinfo.value.kind is ConnectionErrorKind.AUTH
    assert "key file" in str(excinfo.value)
    assert "Security Center" in str(excinfo.value)


def test_password_auth_still_resolves_the_vault_secret(manager, stored_device, credentials):
    username = stored_device.connections["ssh"].username
    credentials.save(stored_device.id, "password", username, "hunter2")

    session = _session(stored_device)
    target = manager._build_target(stored_device, session)

    assert target.secret == "hunter2"
    assert target.private_key is None


# -- device dialog ----------------------------------------------------------
def test_dialog_saves_a_key_file_only_for_key_auth(app, services):
    from nexus_remote.ui.device_dialog import DeviceDialog

    dialog = DeviceDialog(services)
    dialog.name_edit.setText("linux-prod")
    dialog.host_edit.setText("10.10.10.25")
    _enabled, _port, auth, _username, _share = dialog._protocol_widgets["ssh"]
    auth.setCurrentIndex(auth.findData("ssh_key"))
    dialog.key_edit.setText(r"C:\keys\id_ed25519")

    device = dialog._build_device()

    assert device.connections["ssh"].extra["key_filename"] == r"C:\keys\id_ed25519"
    assert "key_filename" not in device.connections["sftp"].extra


def test_dialog_drops_a_stale_key_path_when_auth_changes(app, services):
    from nexus_remote.ui.device_dialog import DeviceDialog

    dialog = DeviceDialog(services)
    dialog.name_edit.setText("linux-prod")
    dialog.host_edit.setText("10.10.10.25")
    _enabled, _port, auth, _username, _share = dialog._protocol_widgets["ssh"]
    auth.setCurrentIndex(auth.findData("ssh_key"))
    dialog.key_edit.setText(r"C:\keys\id_ed25519")
    assert "key_filename" in dialog._build_device().connections["ssh"].extra

    auth.setCurrentIndex(auth.findData("credential_vault"))

    assert "key_filename" not in dialog._build_device().connections["ssh"].extra


def test_dialog_prefills_the_key_file(app, services):
    from nexus_remote.devices.models import Device, ProtocolConfig
    from nexus_remote.ui.device_dialog import DeviceDialog

    device = Device(
        name="linux-prod",
        hostname="10.10.10.25",
        connections={
            "ssh": ProtocolConfig(
                "ssh",
                enabled=True,
                auth_method="ssh_key",
                username="deploy",
                extra={"key_filename": r"C:\keys\id_ed25519"},
            )
        },
    ).validate()
    dialog = DeviceDialog(services, device)

    assert dialog.key_edit.text() == r"C:\keys\id_ed25519"
    assert dialog._build_device().connections["ssh"].extra["key_filename"] == r"C:\keys\id_ed25519"
