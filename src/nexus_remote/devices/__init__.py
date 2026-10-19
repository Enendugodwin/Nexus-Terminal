"""Device inventory domain (UI-independent)."""

from nexus_remote.devices.models import (
    AUTH_METHODS,
    CRITICALITIES,
    ENVIRONMENTS,
    OPERATING_SYSTEMS,
    PROTOCOLS,
    ROUTE_METHODS,
    Device,
    ProfileError,
    ProtocolConfig,
    RoutingPreferences,
    ValidationError,
    normalize_host,
)
from nexus_remote.devices.repository import DeviceRepository, DuplicateDeviceError

__all__ = [
    "AUTH_METHODS",
    "CRITICALITIES",
    "ENVIRONMENTS",
    "OPERATING_SYSTEMS",
    "PROTOCOLS",
    "ROUTE_METHODS",
    "Device",
    "DeviceRepository",
    "DuplicateDeviceError",
    "ProfileError",
    "ProtocolConfig",
    "RoutingPreferences",
    "ValidationError",
    "normalize_host",
]
