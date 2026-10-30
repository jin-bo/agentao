from __future__ import annotations

import json
import logging
from typing import Any, Dict

import pytest

from agentao.permissions import PermissionEngine
from agentao.runtime.arg_repair import (
    TAG_BRACKET_BALANCE,
    TAG_DOUBLE_ENCODED,
    TAG_EMPTY,
    TAG_FENCE,
    TAG_LENIENT_JSON,
    TAG_PYTHON_LITERAL,
    TAG_TRAILING_COMMA,
    parse_tool_arguments,
)
from agentao.runtime.tool_planning import ToolCallDecision, ToolCallPlanner
from agentao.tools.base import Tool, ToolRegistry

from tests.support.tool_calls import make_tool_call


class TestEmptyAndNone:
    @pytest.mark.parametrize("raw", [
        pytest.param(None, id="none"),
        pytest.param("", id="empty-string"),
        pytest.param("   \n\t  ", id="whitespace"),
        pytest.param("None", id="literal-None-string"),
        pytest.param("null", id="literal-null-string"),
    ])
    def test_returns_empty_dict(self, raw):
        args, tags = parse_tool_arguments(raw)
        assert args == {}
        assert tags == [TAG_EMPTY]


class TestStrictJSON:
    def test_strict_json_no_repair_tags(self):
        args, tags = parse_tool_arguments('{"file_path": "x.py", "limit": 50}')
        assert args == {"file_path": "x.py", "limit": 50}
        assert tags == []

    def test_already_dict_passes_through(self):
        args, tags = parse_tool_arguments({"a": 1})
        assert args == {"a": 1}
        assert tags == []


class TestCodeFence:
    @pytest.mark.parametrize("raw, expected", [
        pytest.param('```json\n{"file_path": "x.py"}\n```', {"file_path": "x.py"}, id="json-fence"),
        pytest.param('```\n{"k": 1}\n```', {"k": 1}, id="bare-fence"),
        pytest.param('```JSON\n{"k": 1}\n```', {"k": 1}, id="uppercase-json-fence"),
    ])
    def test_fence_is_stripped(self, raw, expected):
        args, tags = parse_tool_arguments(raw)
        assert args == expected
        assert TAG_FENCE in tags

    def test_empty_fence_returns_empty_dict(self):
        args, tags = parse_tool_arguments("```json\n```")
        assert args == {}
        assert TAG_FENCE in tags
        assert TAG_EMPTY in tags


class TestDoubleEncoded:
    def test_doubly_encoded_json(self):
        raw = json.dumps('{"file_path": "a.txt"}')
        args, tags = parse_tool_arguments(raw)
        assert args == {"file_path": "a.txt"}
        assert TAG_DOUBLE_ENCODED in tags

    def test_single_encoded_string_value_does_not_trigger_double_decode(self):
        # ``"hello"`` parses to the bare string ``hello`` — invalid JSON for
        # a second decode pass. Falls through to dict-enforcement and rejects.
        with pytest.raises(ValueError):
            parse_tool_arguments('"hello"')


class TestLenientJSON:
    def test_raw_control_char_in_string_value(self):
        args, tags = parse_tool_arguments('{"text": "line1\nline2"}')
        assert args == {"text": "line1\nline2"}
        assert TAG_LENIENT_JSON in tags


class TestPythonLiteral:
    def test_single_quoted_python_repr(self):
        args, tags = parse_tool_arguments("{'file_path': 'a.txt', 'limit': 50}")
        assert args == {"file_path": "a.txt", "limit": 50}
        assert TAG_PYTHON_LITERAL in tags

    def test_python_none_true_false_preserved(self):
        raw = "{'file_path': 'x.py', 'limit': None, 'force': True, 'dry': False}"
        args, tags = parse_tool_arguments(raw)
        assert args == {
            "file_path": "x.py",
            "limit": None,
            "force": True,
            "dry": False,
        }
        assert TAG_PYTHON_LITERAL in tags


class TestTrailingComma:
    @pytest.mark.parametrize("raw, expected", [
        pytest.param('{"key": "value",}', {"key": "value"}, id="in-object"),
        pytest.param('{"a": [1, 2,]}', {"a": [1, 2]}, id="in-array"),
        pytest.param('{"a": 1, "b": 2,}', {"a": 1, "b": 2}, id="after-several-members"),
        pytest.param('{"a": 1,\n  }', {"a": 1}, id="whitespace-before-brace"),
    ])
    def test_trailing_comma_is_repaired(self, raw, expected):
        args, tags = parse_tool_arguments(raw)
        assert args == expected
        assert TAG_TRAILING_COMMA in tags


class TestBracketBalance:
    def test_bracket_balance_disabled_by_default(self):
        # Truncated args must NOT silently parse without opt-in.
        with pytest.raises(ValueError):
            parse_tool_arguments('{"key": "value"')

    @pytest.mark.parametrize("raw", [
        pytest.param('{"key": "value"', id="unclosed-brace"),
        pytest.param('{"key": "value"}}', id="excess-close"),
    ])
    def test_bracket_balance_repairs_when_enabled(self, raw):
        args, tags = parse_tool_arguments(raw, allow_bracket_balance=True)
        assert args == {"key": "value"}
        assert TAG_BRACKET_BALANCE in tags

    @pytest.mark.parametrize("raw", [
        # Naive count balancing appends ``}]`` for ``{"a": [1, 2`` — wrong
        # order, still invalid. Honest behavior is to raise; the outbound
        # canonicaliser handles this with a last-resort ``{}``.
        pytest.param('{"a": [1, 2', id="unclosed-nested-array"),
        # Mid-value truncation cannot be recovered by bracket counting.
        pytest.param('{"command": "ls -la", "timeout": 30, "background":',
                     id="glm-truncation-mid-value"),
        pytest.param('{"truncated": "val', id="unclosed-string"),
    ])
    def test_bracket_balance_unrepairable_still_raises(self, raw):
        with pytest.raises(ValueError):
            parse_tool_arguments(raw, allow_bracket_balance=True)


class TestRejectNonJsonSerializableLiterals:
    """Codex P2 fix: ``ast.literal_eval`` accepts bytes/set/complex which
    JSON cannot encode. The planner / permission engine / hooks must
    never see such values, so the python-literal tier rejects them."""

    @pytest.mark.parametrize("raw", [
        pytest.param("{'x': b'abc'}", id="bytes"),
        pytest.param("{'x': {1, 2}}", id="set"),
        pytest.param("{'x': {'y': {1, 2}}}", id="nested-set"),
    ])
    def test_a_value_json_cannot_encode_is_rejected(self, raw):
        with pytest.raises(ValueError):
            parse_tool_arguments(raw)

    def test_python_serializable_literal_still_accepted(self):
        # Sanity: clean Python-repr dicts still pass.
        args, tags = parse_tool_arguments("{'x': [1, 2, 3], 'y': None}")
        assert args == {"x": [1, 2, 3], "y": None}
        assert TAG_PYTHON_LITERAL in tags

    def test_python_int_dict_keys_normalized_to_strings(self):
        # ``{1: "x"}`` would crash ``tool.execute(**{1: "x"})`` with
        # "keywords must be strings". The round-trip coerces ``1`` →
        # ``"1"`` so the planner sees JSON-shape kwargs.
        args, tags = parse_tool_arguments("{1: 'x', 2: 'y'}")
        assert args == {"1": "x", "2": "y"}
        assert all(isinstance(k, str) for k in args)
        assert TAG_PYTHON_LITERAL in tags

    def test_python_tuple_values_normalized_to_lists(self):
        # ``('a', 'b')`` would surprise tools doing ``isinstance(v, list)``.
        args, tags = parse_tool_arguments("{'items': ('a', 'b')}")
        assert args == {"items": ["a", "b"]}
        assert isinstance(args["items"], list)
        assert TAG_PYTHON_LITERAL in tags

    def test_python_nested_tuple_normalized(self):
        args, tags = parse_tool_arguments("{'matrix': ((1, 2), (3, 4))}")
        assert args == {"matrix": [[1, 2], [3, 4]]}
        assert TAG_PYTHON_LITERAL in tags


class TestRejectNonDict:
    @pytest.mark.parametrize("raw", [
        '["x", "y"]',
        '"just a string"',
        "42",
        "true",
        "[1, 2, 3]",
    ])
    def test_non_object_top_level_is_rejected(self, raw):
        with pytest.raises(ValueError, match="must parse to an object"):
            parse_tool_arguments(raw)

    @pytest.mark.parametrize("raw", [
        pytest.param("{this is not, json: at-all", id="garbage"),
        pytest.param("```json\n{not valid\n```", id="malformed-after-fence-strip"),
    ])
    def test_unparseable_input_is_rejected(self, raw):
        with pytest.raises(ValueError):
            parse_tool_arguments(raw)

    def test_non_string_non_dict_input_is_rejected(self):
        with pytest.raises(ValueError, match="must be a string or dict"):
            parse_tool_arguments(12345)


class FakeDangerousTool(Tool):
    """Confirmation-required tool with a name not covered by any permission
    preset, so :class:`PermissionEngine` returns ``None`` and the planner
    falls back to ``requires_confirmation`` → ``ASK``."""

    @property
    def name(self) -> str:
        return "fake_dangerous_tool"

    @property
    def description(self) -> str:
        return "tool with no preset coverage; always asks"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return True

    @property
    def is_read_only(self) -> bool:
        return False

    def execute(self, **kwargs) -> str:  # pragma: no cover — planner doesn't run
        return "ok"


@pytest.fixture
def shell_planner(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeDangerousTool())
    engine = PermissionEngine(project_root=tmp_path)
    logger = logging.getLogger("test.planner")
    return ToolCallPlanner(registry, engine, logger)


def test_repaired_fenced_args_still_require_confirmation(shell_planner):
    tool_call = make_tool_call(
        "call-1", "fake_dangerous_tool", '```json\n{"command": "ls"}\n```'
    )

    result = shell_planner.plan([tool_call])

    assert result.early_messages == []
    assert len(result.plans) == 1
    plan = result.plans[0]
    assert plan.function_args == {"command": "ls"}
    assert plan.decision == ToolCallDecision.ASK


def test_repaired_python_literal_args_still_require_confirmation(shell_planner):
    tool_call = make_tool_call(
        "call-2", "fake_dangerous_tool", "{'command': 'echo hi'}"
    )

    result = shell_planner.plan([tool_call])

    assert result.early_messages == []
    assert len(result.plans) == 1
    assert result.plans[0].function_args == {"command": "echo hi"}
    assert result.plans[0].decision == ToolCallDecision.ASK


def test_unparseable_args_become_early_error_message(shell_planner):
    tool_call = make_tool_call("call-3", "fake_dangerous_tool", "{this is not json")

    result = shell_planner.plan([tool_call])

    assert result.plans == []
    assert len(result.early_messages) == 1
    msg = result.early_messages[0]
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call-3"
    assert "could not parse arguments" in msg["content"]
    # The model must NOT see the word "repaired" — it would teach it to
    # keep emitting bad JSON.
    assert "repair" not in msg["content"].lower()


def test_repair_emits_warning_log(shell_planner, caplog):
    tool_call = make_tool_call(
        "call-4", "fake_dangerous_tool", '```json\n{"command": "ls"}\n```'
    )

    with caplog.at_level(logging.WARNING, logger="test.planner"):
        shell_planner.plan([tool_call])

    repair_logs = [r for r in caplog.records if "repaired via" in r.getMessage()]
    assert len(repair_logs) == 1
    assert TAG_FENCE in repair_logs[0].getMessage()


def test_strict_json_emits_no_repair_warning(shell_planner, caplog):
    tool_call = make_tool_call("call-5", "fake_dangerous_tool", '{"command": "ls"}')

    with caplog.at_level(logging.WARNING, logger="test.planner"):
        shell_planner.plan([tool_call])

    assert not any("repaired via" in r.getMessage() for r in caplog.records)


def test_three_distinct_parse_failures_trip_doom_loop(shell_planner):
    # Each garbage payload is unique, so the identical-args doom-loop
    # cannot fire — only the parse-failure counter should.
    bad_payloads = [
        "{garbage 1",
        "{garbage 2 different",
        "{garbage 3 also different",
    ]

    last_result = None
    for i, payload in enumerate(bad_payloads):
        tc = make_tool_call(f"call-pf-{i}", "fake_dangerous_tool", payload)
        last_result = shell_planner.plan([tc])

    assert last_result is not None
    assert last_result.doom_loop_triggered is True
    assert last_result.plans == []
    assert len(last_result.early_messages) == 1
    assert "doom-loop" in last_result.early_messages[0]["content"].lower()


def test_doom_loop_emits_placeholder_for_every_tool_call_in_batch(tmp_path):
    """Codex P2 fix: when doom-loop triggers mid-batch, the runner must
    still emit a tool_result for *every* tool_call in the assistant
    message — strict Chat-Completions APIs reject the next request if
    any assistant tool_call is missing its matching tool result."""
    from agentao.runtime.tool_runner import ToolRunner
    from agentao.transport import NullTransport
    from agentao.runtime.tool_planning import DOOM_LOOP_THRESHOLD

    registry = ToolRegistry()
    registry.register(FakeDangerousTool())
    engine = PermissionEngine(project_root=tmp_path)
    transport = NullTransport()
    logger = logging.getLogger("test.doom_batch")
    runner = ToolRunner(registry, engine, transport, logger)

    # Trip the identical-args doom counter to N-1 with single-call batches.
    repeating_args = '{"command":"ls"}'
    for i in range(DOOM_LOOP_THRESHOLD - 1):
        runner._planner._doom_counter[
            ("fake_dangerous_tool", repeating_args)
        ] += 1

    # Now a 3-call batch: the FIRST tool_call trips doom; the 2nd and 3rd
    # never reach planning. All three must still get a tool_result.
    batch = [
        make_tool_call("c-trip", "fake_dangerous_tool", repeating_args),
        make_tool_call("c-after-1", "fake_dangerous_tool", '{"command":"echo y"}'),
        make_tool_call("c-after-2", "fake_dangerous_tool", '{"command":"echo z"}'),
    ]
    doom_triggered, results = runner.execute(batch)

    assert doom_triggered is True
    result_ids = [r["tool_call_id"] for r in results]
    assert set(result_ids) == {"c-trip", "c-after-1", "c-after-2"}, (
        "Every tool_call in the batch must have a matching tool_result, "
        "otherwise the next API request is invalid."
    )
    # Strict APIs also reject *duplicate* tool_results for the same id —
    # early_messages already answers the offending call, so the
    # placeholder loop must not double-answer it.
    assert len(result_ids) == len(set(result_ids)), (
        f"Duplicate tool_result for the same tool_call_id: {result_ids}"
    )


def test_parse_failure_doom_loop_emits_placeholder_for_every_tool_call_in_batch(
    tmp_path,
):
    """Same invariant as above, but for the parse-failure doom path."""
    from agentao.runtime.tool_runner import ToolRunner
    from agentao.transport import NullTransport

    registry = ToolRegistry()
    registry.register(FakeDangerousTool())
    engine = PermissionEngine(project_root=tmp_path)
    transport = NullTransport()
    logger = logging.getLogger("test.parse_doom_batch")
    runner = ToolRunner(registry, engine, transport, logger)

    # Push the consecutive-parse-failures counter to N-1 first.
    runner._planner._consecutive_parse_failures["fake_dangerous_tool"] = 2

    batch = [
        make_tool_call("c-trip", "fake_dangerous_tool", "{garbage"),
        make_tool_call("c-after-1", "fake_dangerous_tool", "{still garbage"),
        make_tool_call("c-after-2", "fake_dangerous_tool", '{"command":"ok"}'),
    ]
    doom_triggered, results = runner.execute(batch)

    assert doom_triggered is True
    result_ids = [r["tool_call_id"] for r in results]
    assert set(result_ids) == {"c-trip", "c-after-1", "c-after-2"}
    assert len(result_ids) == len(set(result_ids)), (
        f"Duplicate tool_result for the same tool_call_id: {result_ids}"
    )


def test_successful_parse_resets_parse_failure_streak(shell_planner):
    # Two failures, then a success, then two more failures: should NOT
    # trip — the success reset the counter.
    sequence = [
        ("a", "{bad 1", False),
        ("b", "{bad 2", False),
        ("c", '{"command": "ok"}', True),
        ("d", "{bad 3", False),
        ("e", "{bad 4", False),
    ]
    for cid, payload, _ok in sequence:
        tc = make_tool_call(f"call-{cid}", "fake_dangerous_tool", payload)
        result = shell_planner.plan([tc])
        assert result.doom_loop_triggered is False
