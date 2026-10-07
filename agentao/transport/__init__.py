"""Transport layer — decouples Agentao core runtime from UI and transport implementations."""

from .broadcast import EventBroadcaster
from .confirmation import gate_note
from .events import AgentEvent, EventType
from .base import CoreTransport, Transport
from .non_interactive import NonInteractiveTransport
from .null import NullTransport
from .sdk import SdkTransport, build_compat_transport

__all__ = [
    "AgentEvent",
    "EventType",
    "CoreTransport",
    "Transport",
    "NonInteractiveTransport",
    "NullTransport",
    "SdkTransport",
    "EventBroadcaster",
    "build_compat_transport",
    "gate_note",
]
