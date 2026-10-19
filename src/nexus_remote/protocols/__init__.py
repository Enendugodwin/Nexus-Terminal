"""Protocol adapters and the registry that builds them.

Adding a protocol means adding one module that implements
:class:`~nexus_remote.connections.interfaces.ProtocolAdapter` and registering it
in :func:`adapter_factories`; nothing else in the application changes.
"""

from __future__ import annotations

from typing import Any, Callable

from nexus_remote.connections.interfaces import ProtocolAdapter
from nexus_remote.protocols.rdp import RDPAdapter, rdp_client_path
from nexus_remote.protocols.sftp import SFTPSession
from nexus_remote.protocols.smb import SMBAdapter, smb_available
from nexus_remote.protocols.ssh import ParamikoSSHAdapter, ShellChannel

__all__ = [
    "ParamikoSSHAdapter",
    "RDPAdapter",
    "SFTPSession",
    "SMBAdapter",
    "ShellChannel",
    "adapter_factories",
    "rdp_client_path",
    "register_adapters",
    "smb_available",
]


def adapter_factories(known_hosts: Any = None) -> dict[str, Callable[[], ProtocolAdapter]]:
    """Return the protocol -> adapter factory mapping used by the session manager."""
    return {
        "ssh": lambda: ParamikoSSHAdapter(known_hosts=known_hosts),
        "sftp": lambda: SFTPSession(known_hosts=known_hosts),
        "rdp": RDPAdapter,
        "smb": SMBAdapter,
    }


def register_adapters(manager: Any, known_hosts: Any = None) -> list[str]:
    """Register every built-in adapter with a session manager; returns protocols."""
    factories = adapter_factories(known_hosts)
    for protocol, factory in factories.items():
        manager.register_adapter(protocol, factory)
    return sorted(factories)

