"""Serial console: port-name validation, adapter I/O and device dialog fields."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import serial

from nexus_remote.connections.interfaces import (
    ConnectionErrorKind,
    ConnectionTarget,
    ProtocolError,
    SessionState,
)
from nexus_remote.devices.models import (
    Device,
    ProtocolConfig,
    ValidationError,
    is_serial_port,
    normalize_serial_port,
)
from nexus_remote.protocols.serial import SerialAdapter, available_serial_ports


class FakePort:
    def __init__(self, **kwargs) -> None:
        self.options = kwargs
        self.is_open = True
        self.in_waiting = 0
        self.buffer = b""
        self.written: list[bytes] = []
        self.flushed = 0
        self.name = str(kwargs.get("port", ""))

    def write(self, payload: bytes) -> int:
        self.written.append(payload)
        return len(payload)

    def flush(self) -> None:
        self.flushed += 1

    def read(self, size: int) -> bytes:
        chunk, self.buffer = self.buffer[:size], self.buffer[size:]
        self.in_waiting = len(self.buffer)
        return chunk

    def close(self) -> None:
        self.is_open = False

    def open(self) -> None:
        self.is_open = True


def _target(**options) -> ConnectionTarget:
    return ConnectionTarget(host="COM3", protocol="serial", options=options)


def test_serial_port_names_are_recognised_and_canonicalised():
    assert is_serial_port("COM3")
    assert is_serial_port("com12")
    assert is_serial_port(r"\\.\COM4")
    assert is_serial_port("/dev/ttyUSB0")
    assert is_serial_port("/dev/ttyS0")
    assert not is_serial_port("10.0.0.5")
    assert not is_serial_port("example.com")

    assert normalize_serial_port("com12") == "COM12"
    assert normalize_serial_port(r"\\.\COM4") == "COM4"
    assert normalize_serial_port("/dev/ttyUSB0") == "/dev/ttyUSB0"
    assert normalize_serial_port("10.0.0.5") is None


def test_serial_only_device_validates_with_a_port_name():
    device = Device(
        name="switch-console",
        hostname="COM3",
        connections={
            "ssh": ProtocolConfig("ssh", enabled=False),
            "serial": ProtocolConfig(
                "serial", enabled=True, extra={"port_name": "COM3", "baud": 9600}
            ),
        },
    ).validate()

    assert device.hostname == "COM3"
    assert device.primary_protocol() == "serial"
    assert device.endpoint() == ("COM3", None)


def test_serial_device_defaults_to_a_disabled_connection():
    device = Device(name="server", hostname="10.0.0.5").validate()

    assert "serial" in device.connections
    assert device.connections["serial"].enabled is False


def test_bad_hostname_is_still_rejected():
    with pytest.raises(ValidationError):
        Device(name="bad", hostname="not a host!").validate()


def test_adapter_maps_settings_and_reads_without_blocking():
    created: dict[str, FakePort] = {}

    def factory(**kwargs) -> FakePort:
        port = FakePort(**kwargs)
        created["port"] = port
        return port

    adapter = SerialAdapter(port_factory=factory)
    result = adapter.connect(
        _target(port_name="COM5", baud=115200, data_bits=7, parity="even", stop_bits="2", flow="rtscts")
    )
    options = created["port"].options

    assert result.ok and "COM5" in result.message
    assert options["port"] == "COM5"
    assert options["baudrate"] == 115200
    assert options["bytesize"] == 7
    assert options["parity"] == serial.PARITY_EVEN
    assert options["stopbits"] == serial.STOPBITS_TWO
    assert options["rtscts"] is True
    assert options["timeout"] == 0

    channel = adapter.open_shell()
    created["port"].buffer = b"login: "
    created["port"].in_waiting = 7
    assert channel.read() == b"login: "
    assert channel.read() == b""  # nothing waiting -> never blocks
    channel.write("admin\r")
    assert created["port"].written == [b"admin\r"]
    assert created["port"].flushed == 1

    adapter.close()
    assert channel.closed is True


@pytest.mark.parametrize(
    "options",
    [
        {"data_bits": 9},
        {"parity": "sideways"},
        {"stop_bits": "3"},
        {"flow": "telepathy"},
    ],
)
def test_adapter_rejects_invalid_line_settings(options):
    adapter = SerialAdapter(port_factory=lambda **kwargs: FakePort(**kwargs))

    with pytest.raises(ProtocolError) as excinfo:
        adapter.connect(_target(**options))

    assert excinfo.value.kind is ConnectionErrorKind.POLICY


def test_adapter_requires_a_port_name():
    adapter = SerialAdapter(port_factory=lambda **kwargs: FakePort(**kwargs))
    target = ConnectionTarget(host="", protocol="serial")

    with pytest.raises(ProtocolError) as excinfo:
        adapter.connect(target)

    assert excinfo.value.kind is ConnectionErrorKind.POLICY


def test_adapter_reports_open_failures():
    def factory(**_kwargs):
        raise OSError("access denied")

    adapter = SerialAdapter(port_factory=factory)

    with pytest.raises(ProtocolError) as excinfo:
        adapter.connect(_target())

    assert excinfo.value.kind is ConnectionErrorKind.REFUSED
    assert "COM3" in str(excinfo.value)


def test_adapter_cannot_execute_commands():
    adapter = SerialAdapter(port_factory=lambda **kwargs: FakePort(**kwargs))
    adapter.connect(_target())

    with pytest.raises(ProtocolError) as excinfo:
        adapter.execute("uname -a")

    assert excinfo.value.kind is ConnectionErrorKind.POLICY
    adapter.close()


def test_available_ports_returns_pairs():
    ports = available_serial_ports()

    assert isinstance(ports, list)
    for entry in ports:
        assert len(entry) == 2


# -- device dialog ----------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_dialog_saves_serial_settings(app, services):
    from nexus_remote.ui.device_dialog import DeviceDialog

    dialog = DeviceDialog(services)
    dialog.name_edit.setText("switch-console")
    dialog.host_edit.setText("")  # falls back to the port name
    for _enabled, _port, _auth, _username, _share in dialog._protocol_widgets.values():
        _enabled.setChecked(False)
    dialog.serial_enabled.setChecked(True)
    dialog.serial_port_combo.setEditText("COM7")
    dialog.baud_combo.setCurrentText("115200")
    dialog.data_bits_combo.setCurrentText("7")
    dialog.parity_combo.setCurrentText("even")
    dialog.stop_bits_combo.setCurrentText("2")
    dialog.flow_combo.setCurrentText("rtscts")

    device = dialog._build_device()

    assert device.hostname == "COM7"
    assert device.primary_protocol() == "serial"
    serial_cfg = device.connections["serial"]
    assert serial_cfg.enabled is True
    assert serial_cfg.extra == {
        "port_name": "COM7",
        "baud": 115200,
        "data_bits": 7,
        "parity": "even",
        "stop_bits": "2",
        "flow": "rtscts",
    }


def test_dialog_requires_a_port_when_serial_is_enabled(app, services):
    from nexus_remote.devices.models import ValidationError
    from nexus_remote.ui.device_dialog import DeviceDialog

    dialog = DeviceDialog(services)
    dialog.name_edit.setText("console")
    dialog.host_edit.setText("10.0.0.9")
    for _enabled, _port, _auth, _username, _share in dialog._protocol_widgets.values():
        _enabled.setChecked(False)
    dialog.serial_enabled.setChecked(True)
    dialog.serial_port_combo.setEditText("")

    with pytest.raises(ValidationError):
        dialog._build_device()


def test_dialog_prefills_serial_settings(app, services):
    from nexus_remote.ui.device_dialog import DeviceDialog

    device = Device(
        name="router-console",
        hostname="COM9",
        connections={
            "ssh": ProtocolConfig("ssh", enabled=False),
            "serial": ProtocolConfig(
                "serial",
                enabled=True,
                extra={"port_name": "COM9", "baud": 57600, "parity": "odd"},
            ),
        },
    ).validate()
    dialog = DeviceDialog(services, device)

    assert dialog.serial_enabled.isChecked() is True
    assert dialog.serial_port_combo.currentText() == "COM9"
    assert dialog.baud_combo.currentText() == "57600"
    assert dialog.parity_combo.currentText() == "odd"
    assert dialog._build_device().connections["serial"].extra["port_name"] == "COM9"


def test_serial_session_attaches_to_the_terminal_and_skips_system_info(app, services):
    from nexus_remote.ui.workspace import WorkspacePage

    class FakeChannel:
        closed = False

        def write(self, _data) -> None:
            pass

        def read(self, timeout=0.0, max_bytes=65536) -> bytes:
            return b""

        def resize(self, _width, _height) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeAdapter:
        protocol = "serial"

        def open_shell(self, _size=(120, 32)):
            return FakeChannel()

        def close(self) -> None:
            pass

    device = Device(
        name="switch-console",
        hostname="COM7",
        connections={"serial": ProtocolConfig("serial", enabled=True)},
    ).validate()
    stored = services.devices.add(device)
    session = services.sessions.open(stored, "serial")
    session._adapter = FakeAdapter()
    session.state = SessionState.CONNECTED

    workspace = WorkspacePage(services)
    workspace._attach_terminal(session)

    assert workspace.terminal_view.is_attached() is True
    assert workspace.terminal_view.session_id == session.id
    assert "SSH sessions only" in workspace.system_info.status_label.text()
    workspace.terminal_view.detach()
