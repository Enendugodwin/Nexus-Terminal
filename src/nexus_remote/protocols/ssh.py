"""SSH transport adapter (paramiko).

Paramiko provides the transport and channels; interactive terminal *rendering*
is a UI concern (see ``nexus_remote.ui.terminal_view`` in phase 2). Host keys
are verified against :class:`~nexus_remote.security.host_keys.KnownHosts`; an
unknown or changed key aborts the connection.
"""

from __future__ import annotations

import io
import os
import socket
import time
from typing import Any

from nexus_remote.connections.interfaces import (
    CommandResult,
    ConnectionErrorKind,
    ConnectionTarget,
    ConnectResult,
    ProtocolAdapter,
    ProtocolError,
)
from nexus_remote.security.host_keys import KnownHosts, UntrustedHostKeyError, fingerprint_of

DEFAULT_SSH_PORT = 22


class ShellChannel:
    """Thin wrapper over a paramiko interactive channel.

    Kept free of GUI imports so the terminal widget can drive it (or be driven
    by a test) without change.
    """

    def __init__(self, channel: Any, width: int = 120, height: int = 32) -> None:
        self._channel = channel
        self.width = width
        self.height = height
        try:
            self._channel.settimeout(0.0)
            self._channel.resize_pty(width=width, height=height)
        except Exception:  # pragma: no cover - server dependent
            pass

    @property
    def closed(self) -> bool:
        return bool(self._channel.closed)

    def write(self, data: str | bytes) -> int:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        return int(self._channel.send(payload))

    def read(self, timeout: float = 0.2, max_bytes: int = 65536) -> bytes:
        """Read pending output; returns ``b""`` when nothing arrived in time."""
        try:
            self._channel.settimeout(timeout)
            if self._channel.recv_ready():
                return self._channel.recv(max_bytes)
            if self._channel.recv_stderr_ready():
                return self._channel.recv_stderr(max_bytes)
        except socket.timeout:
            return b""
        except Exception:  # pragma: no cover - channel closed underneath us
            return b""
        return b""

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        try:
            self._channel.resize_pty(width=width, height=height)
        except Exception:  # pragma: no cover - server dependent
            pass

    def close(self) -> None:
        try:
            self._channel.close()
        except Exception:  # pragma: no cover - defensive
            pass


class ParamikoSSHAdapter(ProtocolAdapter):
    """SSH via paramiko, with strict host-key verification by default."""

    protocol = "ssh"

    def __init__(
        self,
        known_hosts: KnownHosts | None = None,
        *,
        connect_timeout: float = 15.0,
        auth_timeout: float = 20.0,
        banner_timeout: float = 20.0,
        keepalive: int = 30,
        allow_agent: bool = True,
        look_for_keys: bool = True,
    ) -> None:
        super().__init__()
        self.known_hosts = known_hosts
        self.connect_timeout = connect_timeout
        self.auth_timeout = auth_timeout
        self.banner_timeout = banner_timeout
        self.keepalive = keepalive
        self.allow_agent = allow_agent
        self.look_for_keys = look_for_keys
        self._client: Any = None
        self._target: ConnectionTarget | None = None

    # -- connect ------------------------------------------------------------
    def connect(self, target: ConnectionTarget) -> ConnectResult:
        paramiko = _paramiko()
        client = paramiko.SSHClient()
        if self.known_hosts is not None:
            client.set_missing_host_key_policy(self.known_hosts.paramiko_policy())
        else:
            # Fail closed: without a key store we refuse unknown host keys.
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.load_system_host_keys()

        kwargs: dict[str, Any] = {
            "hostname": target.host,
            "port": int(target.port or DEFAULT_SSH_PORT),
            "username": target.username or None,
            "timeout": self.connect_timeout,
            "auth_timeout": self.auth_timeout,
            "banner_timeout": self.banner_timeout,
            "allow_agent": self.allow_agent,
            "look_for_keys": self.look_for_keys,
        }
        auth_method = str(target.options.get("auth_method", "credential_vault"))
        if target.secret and auth_method != "ssh_agent":
            kwargs["password"] = target.secret
        if auth_method == "ssh_agent":
            kwargs["look_for_keys"] = False
        key_filename = target.options.get("key_filename")
        if key_filename:
            # The profile may store ~/.ssh/id_ed25519; expand it per user.
            kwargs["key_filename"] = os.path.expanduser(str(key_filename))
        if target.private_key:
            kwargs["pkey"] = _load_private_key(paramiko, target.private_key, target.passphrase)
        jump_socket = target.options.get("jump_socket")
        if target.jump_host and jump_socket is None:
            raise ProtocolError(
                "jump-host routing requires an established tunnel to the jump host",
                kind=ConnectionErrorKind.POLICY,
                detail=(
                    "Configure the jump host as its own device profile; the routing engine opens the "
                    "tunnel first and passes it as options['jump_socket']."
                ),
            )
        if jump_socket is not None:
            kwargs["sock"] = jump_socket

        started = time.perf_counter()
        try:
            client.connect(**kwargs)
        except UntrustedHostKeyError as exc:
            if self.known_hosts is not None:
                self.known_hosts.record_pending(exc.verdict, exc.key)
            raise ProtocolError(
                str(exc),
                kind=ConnectionErrorKind.HOST_KEY,
                detail=exc.verdict.fingerprint,
            ) from exc
        except paramiko.AuthenticationException as exc:
            raise ProtocolError(
                f"authentication failed for {target.username or 'the anonymous user'}",
                kind=ConnectionErrorKind.AUTH,
                detail=str(exc),
            ) from exc
        except paramiko.BadHostKeyException as exc:
            raise ProtocolError(
                f"host key for {target.host} does not match the stored key",
                kind=ConnectionErrorKind.HOST_KEY,
                detail=str(exc),
            ) from exc
        except paramiko.SSHException as exc:
            raise ProtocolError(
                f"SSH negotiation with {target.endpoint()} failed",
                kind=ConnectionErrorKind.PROTOCOL,
                detail=str(exc),
            ) from exc
        except socket.gaierror as exc:
            raise ProtocolError(
                f"could not resolve {target.host}",
                kind=ConnectionErrorKind.DNS,
                detail=str(exc),
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ProtocolError(
                f"connection to {target.endpoint()} timed out",
                kind=ConnectionErrorKind.TIMEOUT,
                detail=str(exc),
                retryable=True,
            ) from exc
        except ConnectionRefusedError as exc:
            raise ProtocolError(
                f"{target.endpoint()} refused the connection",
                kind=ConnectionErrorKind.REFUSED,
                detail=str(exc),
            ) from exc
        except OSError as exc:
            raise ProtocolError(
                f"could not reach {target.endpoint()}: {exc}",
                kind=_classify_os_error(exc),
                detail=str(exc),
                retryable=True,
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000

        self._client = client
        self._target = target
        self._connected = True
        transport = client.get_transport()
        banner = ""
        fingerprint = ""
        if transport is not None:
            banner = getattr(transport, "remote_version", "") or ""
            if self.keepalive:
                transport.set_keepalive(self.keepalive)
            try:
                fingerprint = fingerprint_of(transport.get_remote_server_key())
            except Exception:  # pragma: no cover - defensive
                fingerprint = ""
        return ConnectResult(
            ok=True,
            message=f"SSH session established with {target.endpoint()} in {elapsed_ms:.0f} ms",
            server_banner=banner,
            auth_method=auth_method,
            host_key_fingerprint=fingerprint,
            metadata={"elapsed_ms": elapsed_ms},
        )

    # -- commands -----------------------------------------------------------
    def execute(self, command: str, timeout: float = 30.0) -> CommandResult:
        client = self._require_client()
        started = time.perf_counter()
        try:
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()
        except Exception as exc:
            raise ProtocolError(
                f"command execution failed: {exc}",
                kind=ConnectionErrorKind.PROTOCOL,
                detail=str(exc),
            ) from exc
        return CommandResult(
            command=command,
            exit_code=exit_code,
            stdout=out,
            stderr=err,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def open_shell(self, term_size: tuple[int, int] = (120, 32)) -> ShellChannel:
        client = self._require_client()
        width, height = term_size
        try:
            channel = client.invoke_shell(width=width, height=height)
        except Exception as exc:
            raise ProtocolError(
                f"could not open an interactive shell: {exc}",
                kind=ConnectionErrorKind.PROTOCOL,
                detail=str(exc),
            ) from exc
        return ShellChannel(channel, width=width, height=height)

    # -- teardown -----------------------------------------------------------
    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - defensive
                pass
        self._client = None
        self._connected = False

    # -- helpers ------------------------------------------------------------
    def _require_client(self) -> Any:
        if self._client is None or not self._connected:
            raise ProtocolError("the SSH session is not connected", kind=ConnectionErrorKind.PROTOCOL)
        return self._client

    @property
    def transport(self) -> Any:
        return self._client.get_transport() if self._client is not None else None

    def create_jump_socket(self, host: str, port: int) -> Any:
        """Open a direct-tcpip channel through this connection (ProxyJump).

        Used by the routing engine in phase 7 to reach a second hop without
        giving the second connection its own credentials for the first.
        """
        transport = self.transport
        if transport is None:
            raise ProtocolError("not connected to the jump host", kind=ConnectionErrorKind.PROTOCOL)
        return transport.open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0))


def _paramiko() -> Any:
    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ProtocolError(
            "paramiko is not installed; run: pip install paramiko",
            kind=ConnectionErrorKind.POLICY,
        ) from exc
    return paramiko


def _load_private_key(paramiko: Any, key_text: str, passphrase: str | None) -> Any:
    """Load an in-memory private key (the contents come from the vault)."""
    last_error: Exception | None = None
    key_classes = [
        getattr(paramiko, name, None)
        for name in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey")
    ]
    for key_class in [candidate for candidate in key_classes if candidate is not None]:
        try:
            return key_class.from_private_key(io.StringIO(key_text), password=passphrase)
        except Exception as exc:  # try the next key type
            last_error = exc
    raise ProtocolError(
        "the stored private key could not be loaded (wrong type or passphrase)",
        kind=ConnectionErrorKind.AUTH,
        detail=str(last_error) if last_error else "",
    )


def _classify_os_error(exc: OSError) -> ConnectionErrorKind:
    errno = getattr(exc, "errno", None)
    if errno in (110, 10060):  # ETIMEDOUT, WSAETIMEDOUT
        return ConnectionErrorKind.TIMEOUT
    if errno in (101, 113, 10051, 10065):  # ENETUNREACH, EHOSTUNREACH, WSA*
        return ConnectionErrorKind.ROUTE
    if errno in (111, 10061):  # ECONNREFUSED, WSAECONNREFUSED
        return ConnectionErrorKind.REFUSED
    if errno in (104, 10054):  # ECONNRESET
        return ConnectionErrorKind.PROTOCOL
    return ConnectionErrorKind.UNKNOWN
