"""Tests for ``AgentEvent.schema_version`` and the wire-payload contract.

Issue #7 introduced a versioned wire payload for runtime events. These
tests cover the three guarantees:

1. The default ``schema_version`` is ``1`` on a freshly constructed
   :class:`AgentEvent`, regardless of the event type or whether ``data``
   was supplied.
2. :meth:`AgentEvent.to_dict` produces the canonical three-key
   ``{"type", "schema_version", "data"}`` wire shape with the enum
   coerced to its string value.
3. ACP ``session/update`` notifications carry the field through to
   the actual wire payload an ACP client would observe.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from agentao.acp.protocol import METHOD_SESSION_UPDATE
from agentao.acp.transport import ACPTransport
from agentao.transport.events import AgentEvent, EventType


# ---------------------------------------------------------------------------
# Field defaults
# ---------------------------------------------------------------------------


def test_default_schema_version_is_one_on_concrete_event():
    """Acceptance criterion: ``AgentEvent(...).schema_version == 1``."""
    assert AgentEvent(EventType.TURN_START).schema_version == 1


def test_default_schema_version_independent_of_event_type():
    for etype in EventType:
        assert AgentEvent(etype).schema_version == 1, etype


def test_schema_version_can_be_overridden_explicitly():
    # Future schema bumps will pass a higher integer through the
    # ``schema_version=`` kwarg. Constructing an event with the
    # next-version number must not be rejected by the dataclass.
    evt = AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}, schema_version=2)
    assert evt.schema_version == 2


# ---------------------------------------------------------------------------
# to_dict() wire-payload shape
# ---------------------------------------------------------------------------


def test_to_dict_emits_three_key_shape():
    evt = AgentEvent(EventType.LLM_TEXT, {"chunk": "hello"})
    payload = evt.to_dict()
    assert set(payload.keys()) == {"type", "schema_version", "data"}


def test_to_dict_renders_event_type_as_string_value():
    """``type`` must be the underlying enum string so the dict is JSON-native."""
    evt = AgentEvent(EventType.TOOL_START, {"tool": "read_file", "call_id": "x"})
    payload = evt.to_dict()
    assert payload["type"] == "tool_start"
    assert payload["schema_version"] == 1
    assert payload["data"] == {"tool": "read_file", "call_id": "x"}


def test_to_dict_preserves_data_payload_verbatim():
    data = {"agent": "investigator", "turns": 5, "nested": {"k": [1, 2, 3]}}
    evt = AgentEvent(EventType.AGENT_END, data)
    payload = evt.to_dict()
    assert payload["data"] == data


def test_to_dict_round_trips_through_json():
    """The wire form must encode under ``json.dumps`` without an extra hook."""
    import json

    evt = AgentEvent(EventType.THINKING, {"text": "let me think"})
    encoded = json.dumps(evt.to_dict())
    parsed = json.loads(encoded)
    assert parsed == {
        "type": "thinking",
        "schema_version": 1,
        "data": {"text": "let me think"},
    }


# ---------------------------------------------------------------------------
# ACP wire payload carries schema_version
# ---------------------------------------------------------------------------


class _RecordingServer:
    """Stand-in for AcpServer that captures (method, params) tuples."""

    def __init__(self) -> None:
        self.notifications: List[Tuple[str, Dict[str, Any]]] = []

    def write_notification(self, method: str, params: Dict[str, Any]) -> None:
        self.notifications.append((method, params))


@pytest.fixture
def acp_pair():
    server = _RecordingServer()
    return ACPTransport(server=server, session_id="sess_v"), server


def test_acp_emit_stamps_schema_version_on_update(acp_pair):
    transport, server = acp_pair
    transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}))

    assert len(server.notifications) == 1
    method, params = server.notifications[0]
    assert method == METHOD_SESSION_UPDATE
    update = params["update"]
    assert update["schema_version"] == 1
    # The ACP-shaped fields still need to be present alongside.
    assert update["sessionUpdate"] == "agent_message_chunk"


def test_acp_emit_forwards_custom_schema_version(acp_pair):
    """A future v2 event must surface the bumped version on the wire."""
    transport, server = acp_pair
    transport.emit(
        AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}, schema_version=2)
    )
    update = server.notifications[0][1]["update"]
    assert update["schema_version"] == 2


def test_acp_emit_stamps_schema_version_on_every_notification(acp_pair):
    transport, server = acp_pair
    transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "a"}))
    transport.emit(AgentEvent(EventType.THINKING, {"text": "b"}))
    transport.emit(
        AgentEvent(
            EventType.TOOL_START,
            {"tool": "read_file", "call_id": "u", "args": {}},
        )
    )
    transport.emit(
        AgentEvent(
            EventType.TOOL_COMPLETE,
            {"tool": "read_file", "call_id": "u", "status": "ok"},
        )
    )

    assert len(server.notifications) == 4
    for _, params in server.notifications:
        assert params["update"]["schema_version"] == 1


def test_acp_silent_events_do_not_emit_anything(acp_pair):
    """TURN_START and TOOL_CONFIRMATION must remain silent — the
    schema_version stamp must not synthesize a notification for them."""
    transport, server = acp_pair
    transport.emit(AgentEvent(EventType.TURN_START))
    transport.emit(
        AgentEvent(EventType.TOOL_CONFIRMATION, {"tool": "bash", "args": {}})
    )
    assert server.notifications == []
