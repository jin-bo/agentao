"""Deprecated alias — see :mod:`agentao.host.replay_projection`.

``HarnessReplaySink`` / ``harness_event_to_replay_*`` /
``replay_payload_to_harness_event`` are kept as aliases for the renamed
host-prefixed names until 0.5.0.
"""

from agentao.host.replay_projection import *  # noqa: F401,F403
from agentao.host.replay_projection import (
    HostReplaySink as _HostReplaySink,
    __all__ as _host_all,
    host_event_to_replay_kind as _host_event_to_replay_kind,
    host_event_to_replay_payload as _host_event_to_replay_payload,
    replay_payload_to_host_event as _replay_payload_to_host_event,
)

HarnessReplaySink = _HostReplaySink
harness_event_to_replay_kind = _host_event_to_replay_kind
harness_event_to_replay_payload = _host_event_to_replay_payload
replay_payload_to_harness_event = _replay_payload_to_host_event

__all__ = list(_host_all) + [
    "HarnessReplaySink",
    "harness_event_to_replay_kind",
    "harness_event_to_replay_payload",
    "replay_payload_to_harness_event",
]
