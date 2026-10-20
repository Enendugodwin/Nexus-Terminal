"""Serial console transport (COM ports / POSIX ttys).

A serial session is a raw terminal: there is no login, no command execution and
no authentication. The operator types straight into the device, exactly as with
a physical console cable, so no command passes through the policy gate.

pyserial is used with non-blocking reads because the terminal widget polls on
the GUI timer; blocking for a full read timeout there would freeze the window.
"""

from __future__ import annotations

from typing import Any

from nexus_remote.connections.interfaces import (
    CommandResult,
    ConnectionErrorKind,
    ConnectionTarget,
    ConnectResult,
    ProtocolAdapter,
    ProtocolError,
)

BAUD_RATES = (300, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
DATA_BITS = (5, 6, 7, 8)
PARITIES = ("none", "even", "odd", "mark", "space")
STOP_BITS = ("1", "1.5", "2")
FLOWS = ("none", "xonxoff", "rtscts", "dsrdtr")

DEFAULT_BAUD = 9600


class SerialChannel:
    """Terminal channel over an open pyserial port."""

    def __init__(self, port: Any) -> None:
        self._port = port
        self.width = 0
        self.height = 0

    @property
    def closed(self) -> bool:
        return not bool(getattr(self._port, "is_open", False))

    def write(self, data: str | bytes) -> int:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        try:
            written = self._port.write(payload)
            flush = getattr(self._port, "flush", None)
            if flush is not None:
                flush()
            return int(written or len(payload))
        except Exception as exc:  # pragma: no cover - unplugged adapter
            raise ProtocolError(
                f"serial write failed: {exc}",
                kind=ConnectionErrorKind.PROTOCOL,
                detail=str(exc),
            ) from exc

    def read(self, timeout: float = 0.0, max_bytes: int = 65536) -> bytes:
        """Return immediately with whatever was received (never block the UI)."""
        try:
            waiting = int(getattr(self._port, "in_waiting", 0) or 0)
            if waiting <= 0:
                return b""
            return bytes(self._port.read(min(waiting, max_bytes)) or b"")
        except Exception:  # pragma: no cover - unplugged adapter
            return b""

    def resize(self, _width: int, _height: int) -> None:
        return  # a serial line has no PTY size

    def close(self) -> None:
        try:
            self._port.close()
        except Exception:  # pragma: no cover - defensive
            pass


class SerialAdapter(ProtocolAdapter):
    """Open a local COM/tty console (no network, no credentials)."""

    protocol = "serial"

    def __init__(self, port_factory: Any = None) -> None:
        super().__init__()
        self._port: Any = None
        self._port_factory = port_factory

    def connect(self, target: ConnectionTarget) -> ConnectResult:
        serial = _serial_module()
        options = dict(target.options or {})
        port_name = str(options.get("port_name") or target.host or "").strip()
        if not port_name:
            raise ProtocolError(
                "a serial connection needs a port name (for example COM3 or /dev/ttyUSB0)",
                kind=ConnectionErrorKind.POLICY,
            )
        baud = _int_option(options.get("baud"), DEFAULT_BAUD, "baud rate")
        bytesize = _int_option(options.get("data_bits"), 8, "data bits")
        if bytesize not in DATA_BITS:
            raise ProtocolError(
                f"data bits must be one of {', '.join(str(v) for v in DATA_BITS)}",
                kind=ConnectionErrorKind.POLICY,
            )
        parity_name = str(options.get("parity", "none")).lower()
        if parity_name not in PARITIES:
            raise ProtocolError(
                f"parity must be one of {', '.join(PARITIES)}", kind=ConnectionErrorKind.POLICY
            )
        stop_name = str(options.get("stop_bits", "1"))
        if stop_name not in STOP_BITS:
            raise ProtocolError(
                f"stop bits must be one of {', '.join(STOP_BITS)}", kind=ConnectionErrorKind.POLICY
            )
        flow = str(options.get("flow", "none")).lower()
        if flow not in FLOWS:
            raise ProtocolError(
                f"flow control must be one of {', '.join(FLOWS)}", kind=ConnectionErrorKind.POLICY
            )

        kwargs = {
            "port": port_name,
            "baudrate": baud,
            "bytesize": bytesize,
            "parity": getattr(serial, f"PARITY_{parity_name.upper()}"),
            "stopbits": _stop_bits_constant(serial, stop_name),
            "timeout": 0,
            "write_timeout": 2,
            "xonxoff": flow == "xonxoff",
            "rtscts": flow == "rtscts",
            "dsrdtr": flow == "dsrdtr",
        }
        factory = self._port_factory or serial.Serial
        try:
            port = factory(**kwargs)
            if not bool(getattr(port, "is_open", True)):
                port.open()
        except Exception as exc:  # serial.SerialException and OS errors
            raise ProtocolError(
                f"could not open {port_name}: {exc}",
                kind=ConnectionErrorKind.REFUSED,
                detail=str(exc),
            ) from exc

        self._port = port
        self._connected = True
        description = str(getattr(port, "name", port_name) or port_name)
        return ConnectResult(
            ok=True,
            message=f"Serial console open on {description} at {baud} baud",
            server_banner=f"{description} {baud} {bytesize}{parity_name[0].upper()}{stop_name}",
            auth_method="none",
            metadata={"port": port_name, "baud": baud, "parity": parity_name, "flow": flow},
        )

    def execute(self, command: str, timeout: float = 30.0) -> CommandResult:
        raise ProtocolError(
            "a serial console cannot execute commands; type into the terminal instead",
            kind=ConnectionErrorKind.POLICY,
        )

    def open_shell(self, term_size: tuple[int, int] = (120, 32)) -> SerialChannel:
        if self._port is None or not self._connected:
            raise ProtocolError("the serial port is not open", kind=ConnectionErrorKind.PROTOCOL)
        channel = SerialChannel(self._port)
        channel.width, channel.height = term_size
        return channel

    def close(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            except Exception:  # pragma: no cover - defensive
                pass
        self._port = None
        self._connected = False


def available_serial_ports() -> list[tuple[str, str]]:
    """Return ``(port, description)`` pairs for the local machine."""
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover - declared dependency
        return []
    try:
        return [
            (str(port.device), str(port.description or ""))
            for port in list_ports.comports()
        ]
    except Exception:  # pragma: no cover - platform quirks
        return []


def _stop_bits_constant(serial: Any, value: str) -> Any:
    mapping = {
        "1": serial.STOPBITS_ONE,
        "1.5": serial.STOPBITS_ONE_POINT_FIVE,
        "2": serial.STOPBITS_TWO,
    }
    return mapping[value]


def _serial_module() -> Any:
    try:
        import serial
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ProtocolError(
            "pyserial is not installed; run: pip install pyserial",
            kind=ConnectionErrorKind.POLICY,
        ) from exc
    return serial


def _int_option(value: object, default: int, label: str) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{label} must be a number", kind=ConnectionErrorKind.POLICY) from exc
    if parsed <= 0:
        raise ProtocolError(f"{label} must be positive", kind=ConnectionErrorKind.POLICY)
    return parsed
