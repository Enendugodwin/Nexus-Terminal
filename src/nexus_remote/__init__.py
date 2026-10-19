"""Nexus Remote - universal remote access and device management workspace.

The package is a modular monolith: the desktop UI depends on the service
modules (devices, connections, diagnostics, assistant, security) through
explicit interfaces, and never the other way around. The optional gateway and
endpoint agent live outside this package as separate deployables.
"""

from nexus_remote.config import APP_NAME, APP_VERSION

__all__ = ["APP_NAME", "APP_VERSION"]
