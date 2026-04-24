"""Session Replay — structured event timeline for debugging and audit.

This module records a per-instance JSONL timeline of runtime events to
``.agentao/replays/<session_id>.<instance_id>.jsonl`` when
``replay.enabled=true`` in ``.agentao/settings.json``.

Replay is intentionally separate from ``save_session/load_session``:
``sessions/`` captures the conversation state needed to resume a chat,
while ``replays/`` captures the operational timeline (tool calls,
permissions, streaming chunks, errors) for inspection and future
protocol replay features.
"""

from .adapter import ReplayAdapter
from .config import REPLAY_DEFAULTS, ReplayConfig, load_replay_config, save_replay_enabled
from .events import (
    SCHEMA_VERSION,
    EventKind,
    ReplayEvent,
)
from .meta import ReplayMeta
from .reader import (
    ReplayReader,
    find_replay_candidates,
    list_replays,
    open_replay,
    resolve_replay_id,
)
from .recorder import ReplayRecorder
from .retention import ReplayRetentionPolicy

__all__ = [
    "SCHEMA_VERSION",
    "EventKind",
    "ReplayEvent",
    "ReplayAdapter",
    "ReplayConfig",
    "ReplayRecorder",
    "ReplayReader",
    "ReplayMeta",
    "ReplayRetentionPolicy",
    "REPLAY_DEFAULTS",
    "load_replay_config",
    "save_replay_enabled",
    "list_replays",
    "open_replay",
    "resolve_replay_id",
    "find_replay_candidates",
]
