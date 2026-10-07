"""A transport's ``ask_user`` reaches the built-in tool through the same
signature check as an ``AskUserTool`` callback (``invoke_ask_user_callback``).

The wiring used to be ``lambda *a, **kw: agent.transport.ask_user(*a, **kw)``:
the check then saw the lambda's ``**kw`` and forwarded every structured hint,
so a transport whose ``ask_user`` takes only the question raised
``TypeError: ... unexpected keyword argument 'header'`` the first time the
model asked. Both sites are covered: the parent's tool and the callback a
sub-agent wrapper hands to its child's transport.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from agentao import Agentao
from agentao.agents.tools import AgentToolWrapper
from agentao.transport import NullTransport, build_compat_transport


class _QuestionOnly(NullTransport):
    def ask_user(self, question):  # the pre-hint signature
        return f"one:{question}"


class _FullSignature(NullTransport):
    def ask_user(self, question, *, header=None, options=None, multiple=False, allow_custom=True):
        return f"full:{question}:{header}:{options}"


def _agent(tmp_path: Path, transport) -> Agentao:
    return Agentao(
        working_directory=tmp_path,
        transport=transport,
        enable_builtin_agents=True,
        logger=logging.getLogger("test-ask-user-signature"),
    )


@pytest.mark.parametrize(
    "transport, expected",
    [(_QuestionOnly(), "one:q?"), (_FullSignature(), "full:q?:H:['a']")],
)
def test_parent_tool_respects_transport_signature(tmp_path, transport, expected):
    agent = _agent(tmp_path, transport)
    try:
        result = agent.tools.get("ask_user").execute(question="q?", header="H", options=["a"])
        assert result == expected
    finally:
        agent.close()


@pytest.mark.parametrize(
    "transport, expected",
    [(_QuestionOnly(), "one:q?"), (_FullSignature(), "full:q?:H:['a']")],
)
def test_subagent_callback_respects_transport_signature(tmp_path, transport, expected):
    agent = _agent(tmp_path, transport)
    try:
        wrappers = [t for t in agent.tools.tools.values() if isinstance(t, AgentToolWrapper)]
        assert wrappers, "enable_builtin_agents=True registered no agent tools"
        callback = wrappers[0]._ask_user_callback
        assert callback("q?", header="H", options=["a"], multiple=False, allow_custom=True) == expected
        # The path a child actually takes: its compat transport wraps the
        # callback, and must hand the hints on rather than drop them.
        child = build_compat_transport(ask_user_callback=callback)
        assert child.ask_user("q?", header="H", options=["a"]) == expected
    finally:
        agent.close()
