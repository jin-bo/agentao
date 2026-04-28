"""Embedded harness factory for :class:`agentao.agent.Agentao`.

`build_from_environment()` captures every implicit env / dotenv / cwd /
``.agentao/*.json`` read that the agent constructor would otherwise
perform and routes them through explicit-injection kwargs. CLI and
ACP entrypoints go through this single surface so embedded hosts that
already have explicit config can construct :class:`Agentao` directly
without any of the env-touching side effects.
"""

from .factory import build_from_environment

__all__ = ["build_from_environment"]
