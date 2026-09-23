"""The tool-name → ACP ``kind`` table must describe the real registry.

The previous table was written from a mental model of agentao's tool set
rather than from the registry. It mapped ``edit_file``, ``edit``,
``read_folder``, ``find_files`` and ``search_text`` — none of which agentao
has ever registered — while the real edit tool (``replace``) and the real
search tool (``search_file_content``) fell through to ``"other"``. Every ACP
client was therefore told that agentao's principal file-editing tool was an
unclassified one, and the only test on the table asserted a mapping for a
phantom tool.

So the table is now exhaustive over ``BUILTIN_TOOL_NAMES`` *by test*, and
its values are checked against the schema's own enum. There is also no
gate anywhere that validates a real ``session/update`` against the schema
it is supposed to satisfy — which is how that drift survived — so the last
class here adds one for the tool-call updates.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, get_args

import pytest

from agentao.acp._transport_helpers import (
    _HOST_TOOL_KIND_ALIASES,
    _TOOL_KIND_MAP,
    _tool_kind,
)
from agentao.acp.schema import AcpSessionUpdateParams, AcpToolKind
from agentao.acp.transport import ACPTransport
from agentao.tooling.registry import BUILTIN_TOOL_NAMES
from agentao.transport.events import AgentEvent, EventType

from .support.acp_server import RecordingServer


# ---------------------------------------------------------------------------
# The table describes the registry
# ---------------------------------------------------------------------------

def test_every_builtin_tool_has_an_explicit_kind():
    """A new built-in must make a decision, not inherit the fallback."""
    missing = sorted(BUILTIN_TOOL_NAMES - set(_TOOL_KIND_MAP))
    assert missing == [], (
        f"built-in tool(s) {missing} have no _TOOL_KIND_MAP entry and would "
        f"be reported to ACP clients as kind 'other'. Add an entry — "
        f"'other' is a fine answer, but it should be a chosen one."
    )


def test_the_table_names_no_tool_that_does_not_exist():
    """The half of the table that claims to be agentao's must be agentao's.

    Names are read off the tool classes rather than written out here, so a
    rename moves this test with the code instead of leaving it asserting a
    phantom — which is precisely what the old table's only test did.
    """
    from agentao.tools.agents import CLIHelpAgentTool, CodebaseInvestigatorTool
    from agentao.tools.goal import UpdateGoalTool
    from agentao.tools.plan import PlanFinalizeTool, PlanSaveTool

    # Registered outside BUILTIN_TOOL_NAMES: agent tools, plan tools, and the
    # CLI's injected update_goal. Anything else in the table is a phantom.
    outside_the_constant = {
        CLIHelpAgentTool.name.fget(None),
        CodebaseInvestigatorTool.name.fget(None),
        PlanSaveTool.name.fget(None),
        PlanFinalizeTool.name.fget(None),
        UpdateGoalTool.name.fget(None),
    }
    phantom = sorted(set(_TOOL_KIND_MAP) - BUILTIN_TOOL_NAMES - outside_the_constant)
    assert phantom == []


@pytest.mark.parametrize("name", sorted(_TOOL_KIND_MAP))
def test_every_agentao_kind_is_a_v1_kind(name):
    assert _TOOL_KIND_MAP[name] in get_args(AcpToolKind)


@pytest.mark.parametrize("name", sorted(_HOST_TOOL_KIND_ALIASES))
def test_every_host_alias_kind_is_a_v1_kind(name):
    assert _HOST_TOOL_KIND_ALIASES[name] in get_args(AcpToolKind)


def test_the_tools_that_were_reported_as_other_are_not(name=None):
    """The specific regression: agentao's edit and search tools."""
    assert _tool_kind("replace") == "edit"
    assert _tool_kind("search_file_content") == "search"


def test_a_host_alias_does_not_shadow_an_agentao_tool():
    """agentao's own answer wins wherever both tables have a name."""
    overlap = set(_TOOL_KIND_MAP) & set(_HOST_TOOL_KIND_ALIASES)
    for name in overlap:
        assert _tool_kind(name) == _TOOL_KIND_MAP[name]


def test_an_unknown_tool_is_other():
    assert _tool_kind("mcp_github_create_issue") == "other"
    assert _tool_kind("") == "other"


# ---------------------------------------------------------------------------
# What the transport emits satisfies the contract it publishes
# ---------------------------------------------------------------------------

class _SessionStub:
    cwd = None


class _SessionsStub:
    def require(self, _session_id: str) -> _SessionStub:
        return _SessionStub()


class _ServerStub(RecordingServer):
    sessions = _SessionsStub()


def _emitted(events: List[AgentEvent]) -> List[Dict[str, Any]]:
    server = _ServerStub()
    transport = ACPTransport(server=server, session_id="s1")
    for event in events:
        transport.emit(event)
    return [params for _method, params in server.notifications]


_TOOL_EVENTS: List[Tuple[str, AgentEvent]] = [
    (
        "tool_call for a plain tool",
        AgentEvent(EventType.TOOL_START, {"tool": "read_file", "call_id": "c1",
                                          "args": {"file_path": "a.py"}}),
    ),
    (
        "tool_call carrying a proposed diff",
        AgentEvent(EventType.TOOL_START, {"tool": "replace", "call_id": "c2",
                                          "args": {"file_path": "/a.py",
                                                   "old_text": "x",
                                                   "new_text": "y"}}),
    ),
    (
        "tool_call for a planning tool",
        # ``think`` is outside the six values this contract used to allow, so
        # this row is what holds the enum aligned with ACP v1.
        AgentEvent(EventType.TOOL_START, {"tool": "plan_save", "call_id": "c5",
                                          "args": {"plan": "do the thing"}}),
    ),
    (
        "tool_call_update with streamed output",
        AgentEvent(EventType.TOOL_OUTPUT, {"tool": "run_shell_command",
                                           "call_id": "c3", "chunk": "hi\n"}),
    ),
    (
        "tool_call_update terminal with an error",
        AgentEvent(EventType.TOOL_COMPLETE, {"tool": "run_shell_command",
                                             "call_id": "c4", "status": "error",
                                             "error": "boom"}),
    ),
]


@pytest.mark.parametrize("label,event", _TOOL_EVENTS, ids=[e[0] for e in _TOOL_EVENTS])
def test_an_emitted_tool_update_validates_against_the_published_schema(label, event):
    params = _emitted([event])
    assert params, f"{label} emitted nothing"
    for one in params:
        AcpSessionUpdateParams.model_validate(one)


def test_every_builtin_kind_is_accepted_by_the_update_model():
    """The drift the old 6-value enum made possible: emit a kind, get rejected."""
    for name in sorted(BUILTIN_TOOL_NAMES):
        params = _emitted([
            AgentEvent(EventType.TOOL_START, {
                "tool": name, "call_id": f"c-{name}", "args": {},
            })
        ])
        # todo_write with no todos falls through to a real tool_call; with
        # todos it becomes a plan and emits nothing here.
        for one in params:
            AcpSessionUpdateParams.model_validate(one)
