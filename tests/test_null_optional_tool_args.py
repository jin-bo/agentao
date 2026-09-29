"""A ``null`` for an optional tool parameter means "not given".

Models trained on strict schemas send ``"offset": null`` for a parameter they
mean to omit. Passed through, ``read_file`` ran ``max(1, None)`` and answered
``Error reading file: '>' not supported between instances of 'NoneType' and
'int'``. The planner now drops such a ``None`` so the tool's own default
applies — unless the parameter is required, or its schema allows ``null``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

import pytest

from agentao import Agentao
from agentao.permissions import PermissionEngine
from agentao.runtime.arg_repair import drop_null_optionals
from agentao.runtime.tool_planning import ToolCallPlanner
from agentao.tools.base import Tool, ToolRegistry
from tests.support.openai_responses_wire import (
    Wire, attach, completed, created, function_call_events, function_call_item,
    message_item, stream_of, text_events,
)
from tests.support.tool_calls import make_tool_call

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "offset": {"type": "integer"},
        "note": {"type": ["string", "null"]},
        "tag": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "legacy": {"type": "string", "nullable": True},
    },
    "required": ["path"],
}


class TestDropNullOptionals:
    def test_an_optional_null_is_dropped(self):
        args, dropped = drop_null_optionals({"path": "a", "offset": None}, SCHEMA)
        assert args == {"path": "a"} and dropped == ["offset"]

    def test_a_required_null_is_kept_to_fail_as_before(self):
        args, dropped = drop_null_optionals({"path": None}, SCHEMA)
        assert args == {"path": None} and dropped == []

    @pytest.mark.parametrize("key", ["note", "tag", "legacy"])
    def test_a_null_the_schema_allows_is_kept(self, key):
        args, dropped = drop_null_optionals({"path": "a", key: None}, SCHEMA)
        assert args == {"path": "a", key: None} and dropped == []

    def test_a_null_for_a_parameter_the_schema_does_not_name_is_dropped(self):
        args, dropped = drop_null_optionals({"path": "a", "extra": None}, SCHEMA)
        assert args == {"path": "a"} and dropped == ["extra"]

    def test_falsy_values_that_are_not_none_are_kept(self):
        given = {"path": "", "offset": 0, "note": False}
        assert drop_null_optionals(given, SCHEMA) == (given, [])

    def test_nothing_dropped_returns_the_same_object(self):
        given = {"path": "a"}
        assert drop_null_optionals(given, SCHEMA)[0] is given

    @pytest.mark.parametrize("schema", [None, "x", {"required": "path"}])
    def test_a_schema_that_cannot_say_what_is_required_leaves_args_alone(self, schema):
        given = {"path": None, "offset": None}
        assert drop_null_optionals(given, schema) == (given, [])

    def test_no_required_list_means_nothing_is_required(self):
        args, _ = drop_null_optionals({"a": None}, {"type": "object"})
        assert args == {}


class _Recorder(Tool):
    """Read-only, so the planner allows it without asking."""

    def __init__(self, schema: Any = SCHEMA) -> None:
        self._schema = schema

    @property
    def name(self) -> str:
        return "recorder"

    @property
    def description(self) -> str:
        return "records its arguments"

    @property
    def parameters(self) -> Dict[str, Any]:
        if isinstance(self._schema, Exception):
            raise self._schema
        return self._schema

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(self, **kwargs) -> str:  # pragma: no cover — planner doesn't run
        return "ok"


def _planner(tmp_path, tool: Tool) -> ToolCallPlanner:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolCallPlanner(registry, PermissionEngine(project_root=tmp_path),
                           logging.getLogger("test.null_args"))


def test_the_planner_drops_it_before_the_plan_is_made(tmp_path, caplog):
    planner = _planner(tmp_path, _Recorder())
    call = make_tool_call("c1", "recorder", json.dumps({"path": "a", "offset": None}))
    with caplog.at_level(logging.INFO, logger="test.null_args"):
        result = planner.plan([call])
    assert result.plans[0].function_args == {"path": "a"}
    assert "dropped null optional argument(s): offset" in caplog.text


def test_a_schema_that_raises_leaves_the_args_as_they_were(tmp_path):
    planner = _planner(tmp_path, _Recorder(RuntimeError("no schema")))
    call = make_tool_call("c1", "recorder", json.dumps({"path": "a", "offset": None}))
    assert planner.plan([call]).plans[0].function_args == {"path": "a", "offset": None}


# -- the built-in that failed, in a real turn ---------------------------------


def _turn(tmp_path: Path, arguments: Dict[str, Any]) -> str:
    """One ``read_file`` call with ``arguments``; returns its tool result."""
    agent = Agentao(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                    api_format="openai-responses", working_directory=tmp_path)
    try:
        raw = json.dumps(arguments)
        attach(agent.llm, Wire(
            stream_of(created(), function_call_events(0, "call_r", "read_file", raw),
                      completed([function_call_item("call_r", "read_file", raw)])),
            stream_of(created(), text_events(0, "done"), completed([message_item("done")])),
        ))
        assert agent.chat("read it") == "done"
        return next(m["content"] for m in agent.messages if m.get("role") == "tool")
    finally:
        agent.close()


def test_read_file_with_null_offset_and_limit_reads_the_whole_file(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("first\nsecond\n", encoding="utf-8")
    result = _turn(tmp_path, {"file_path": str(note), "offset": None, "limit": None})
    assert "first" in result and "second" in result
    assert "not supported" not in result


def test_read_file_with_a_null_file_path_is_still_an_error(tmp_path):
    result = _turn(tmp_path, {"file_path": None})
    assert "error" in result.lower()
