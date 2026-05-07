"""Replay lifecycle — thin compatibility shim around :class:`ReplayManager`.

Originally hosted the ``start_replay`` / ``end_replay`` /
``reload_replay_config`` implementations that mutated agent attributes
directly (``agent._replay_recorder`` etc.). Those attributes are gone in
0.4.5 — :class:`ReplayManager` owns the state. The module is preserved
only so any latent ``from agentao.replay.lifecycle import ...`` keeps
resolving; new code should call ``agent.replay_manager.start()`` etc.
directly. Scheduled for removal in 0.5.0 alongside the agent facade
methods.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .config import ReplayConfig

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..agent import Agentao


def start_replay(
    agent: "Agentao",
    session_id: Optional[str] = None,
) -> Optional[Path]:
    """Delegate to ``agent.replay_manager.start``."""
    return agent._ensure_replay_manager().start(session_id)


def end_replay(agent: "Agentao") -> None:
    """Delegate to ``agent.replay_manager.end``."""
    if agent.replay_manager is not None:
        agent.replay_manager.end()


def reload_replay_config(agent: "Agentao") -> ReplayConfig:
    """Delegate to ``agent.replay_manager.reload_config``."""
    return agent._ensure_replay_manager().reload_config()
