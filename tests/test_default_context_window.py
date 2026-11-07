"""The default context window is one number, stated at four sites.

``ContextManager.DEFAULT_MAX_TOKENS``, ``Agentao(max_context_tokens=)``, the
CLI's ``AGENTAO_CONTEXT_TOKENS`` fallback and the sub-agent fallback in
``agents/tools/_wrapper.py`` must agree, or the CLI and an embedding host get
different compaction thresholds for the same model.
"""

import inspect
from pathlib import Path

from agentao.agent import Agentao
from agentao.context_manager import ContextManager

_PKG = Path(__file__).resolve().parent.parent / "agentao"


def test_default_is_256k():
    assert ContextManager.DEFAULT_MAX_TOKENS == 256_000


def test_agentao_default_matches_context_manager():
    default = inspect.signature(Agentao.__init__).parameters["max_context_tokens"].default
    assert default == ContextManager.DEFAULT_MAX_TOKENS


def test_cli_env_fallback_matches():
    src = (_PKG / "cli" / "app.py").read_text(encoding="utf-8")
    assert f'"AGENTAO_CONTEXT_TOKENS", "{ContextManager.DEFAULT_MAX_TOKENS}"' in src


def test_subagent_fallback_matches():
    src = (_PKG / "agents" / "tools" / "_wrapper.py").read_text(encoding="utf-8")
    assert f"self._max_context_tokens or {ContextManager.DEFAULT_MAX_TOKENS:_}," in src
