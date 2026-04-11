"""Agentao - A CLI agent harness with tools, skills, and MCP support."""

import warnings
from typing import TYPE_CHECKING

warnings.filterwarnings("ignore", message="urllib3.*or chardet.*doesn't match")

__version__ = "0.2.8-rc1"

# Lazy exports via PEP 562 module __getattr__.
#
# Eager imports of `Agentao` / `SkillManager` would pull the entire LLM stack
# (openai, mcp, tools, llm.client) into every consumer of the `agentao` package
# — including standalone subpackages like `agentao.memory` that have no LLM
# dependency. Lazy resolution lets `import agentao.memory` stay lightweight
# and independently testable, while keeping `from agentao import Agentao` and
# `agentao.Agentao(...)` working unchanged.

__all__ = ["Agentao", "SkillManager"]

if TYPE_CHECKING:
    # Type checkers and IDEs see explicit imports; the runtime path uses __getattr__.
    from .agent import Agentao
    from .skills import SkillManager


def __getattr__(name: str):
    if name == "Agentao":
        from .agent import Agentao
        return Agentao
    if name == "SkillManager":
        from .skills import SkillManager
        return SkillManager
    raise AttributeError(f"module 'agentao' has no attribute {name!r}")


def __dir__():
    return sorted(set(list(globals().keys()) + __all__))
