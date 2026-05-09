"""Transport layer — decouples Agentao core runtime from UI and transport implementations."""

from .broadcast import EventBroadcaster
from .events import AgentEvent, EventType
from .base import Transport
from .non_interactive import NonInteractiveTransport
from .null import NullTransport
from .sdk import SdkTransport, build_compat_transport

__all__ = [
    "AgentEvent",
    "EventType",
    "Transport",
    "NonInteractiveTransport",
    "NullTransport",
    "SdkTransport",
    "EventBroadcaster",
    "build_compat_transport",
]
