"""Compatibility path for :class:`~agentao.outcome.TurnOutcome`.

The definition lives in :mod:`agentao.outcome`, a standard-library-only
module. Importing this module runs ``agentao/runtime/__init__.py``, which
loads the chat loop and the LLM client, so new code imports the class from
``agentao.host`` or ``agentao.outcome`` instead. This path re-exports the same
class, so identity checks and existing imports keep working.
"""

from ..outcome import TurnOutcome

__all__ = ["TurnOutcome"]
