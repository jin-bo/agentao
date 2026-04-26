from __future__ import annotations

import logging
from typing import Any, Dict

import pytest

from agentao.permissions import PermissionEngine
from agentao.runtime.name_repair import repair_tool_name
from agentao.runtime.tool_planning import ToolCallDecision, ToolCallPlanner
from agentao.tools.base import Tool, ToolRegistry

from tests.support.tool_calls import make_tool_call


VALID = {
    "todo",
    "patch",
    "browser_click",
    "browser_navigate",
    "web_search",
    "read_file",
    "write_file",
    "terminal",
}


# ---------------------------------------------------------------------------
# Standalone repair_tool_name
# ---------------------------------------------------------------------------


class TestExistingBehaviorStillWorks:
    def test_lowercase_already_matches(self):
        assert repair_tool_name("browser_click", VALID) == "browser_click"

    def test_uppercase_simple(self):
        assert repair_tool_name("TERMINAL", VALID) == "terminal"

    def test_dash_to_underscore(self):
        assert repair_tool_name("web-search", VALID) == "web_search"

    def test_space_to_underscore(self):
        assert repair_tool_name("write file", VALID) == "write_file"

    def test_fuzzy_near_miss(self):
        assert repair_tool_name("terminall", VALID) == "terminal"

    def test_unknown_returns_none(self):
        assert repair_tool_name("xyz_no_such_tool", VALID) is None


class TestClassLikeEmissions:
    """Regression coverage for Claude-style CamelCase + _tool variants."""

    def test_camel_case_no_suffix(self):
        assert repair_tool_name("BrowserClick", VALID) == "browser_click"

    def test_camel_case_with_underscore_tool_suffix(self):
        assert repair_tool_name("BrowserClick_tool", VALID) == "browser_click"

    def test_camel_case_with_class_suffix(self):
        assert repair_tool_name("PatchTool", VALID) == "patch"

    def test_double_tacked_class_and_snake_suffix(self):
        # Hardest case: TodoTool_tool — strip both '_tool' (trailing) and
        # 'Tool' (CamelCase embedded) to reach 'todo'.
        assert repair_tool_name("TodoTool_tool", VALID) == "todo"

    def test_simple_name_with_tool_suffix(self):
        assert repair_tool_name("Patch_tool", VALID) == "patch"

    def test_simple_name_with_dash_tool_suffix(self):
        assert repair_tool_name("patch-tool", VALID) == "patch"

    def test_camel_case_preserves_multi_word_match(self):
        assert repair_tool_name("ReadFile_tool", VALID) == "read_file"
        assert repair_tool_name("WriteFileTool", VALID) == "write_file"

    def test_mixed_separators_and_suffix(self):
        assert repair_tool_name("write-file_Tool", VALID) == "write_file"


class TestEdgeCases:
    def test_empty_string(self):
        assert repair_tool_name("", VALID) is None

    def test_only_tool_suffix(self):
        # '_tool' alone is not a valid name — must not match anything.
        assert repair_tool_name("_tool", VALID) is None

    def test_empty_valid_names(self):
        assert repair_tool_name("anything", set()) is None

    def test_very_long_unrelated_name_does_not_match(self):
        # Fuzzy cutoff 0.7 must reject obviously unrelated names.
        assert repair_tool_name(
            "ThisIsNotRemotelyARealToolName_tool", VALID
        ) is None

    def test_list_valid_names_also_works(self):
        # API accepts any iterable, not just sets.
        assert repair_tool_name("BrowserClick", list(VALID)) == "browser_click"


# ---------------------------------------------------------------------------
# Planner integration: malformed name → repaired and executed (or asked)
# ---------------------------------------------------------------------------


class FakeNamedTool(Tool):
    def __init__(self, name: str, requires_confirm: bool = True):
        super().__init__()
        self._name = name
        self._requires = requires_confirm

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"fake tool {self._name}"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def requires_confirmation(self) -> bool:
        return self._requires

    def execute(self, **kwargs) -> str:  # pragma: no cover
        return "ok"


@pytest.fixture
def planner_with_browser_click(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeNamedTool("browser_click"))
    engine = PermissionEngine(project_root=tmp_path)
    logger = logging.getLogger("test.name_planner")
    return ToolCallPlanner(registry, engine, logger)


def test_planner_repairs_camel_case_tool_name(planner_with_browser_click, caplog):
    tc = make_tool_call("call-1", "BrowserClick_tool")

    with caplog.at_level(logging.WARNING, logger="test.name_planner"):
        result = planner_with_browser_click.plan([tc])

    assert result.early_messages == []
    assert len(result.plans) == 1
    plan = result.plans[0]
    assert plan.function_name == "browser_click"
    assert plan.decision == ToolCallDecision.ASK
    assert any(
        "BrowserClick_tool" in r.getMessage() and "browser_click" in r.getMessage()
        for r in caplog.records
    )


def test_planner_unrepairable_name_still_emits_error(planner_with_browser_click):
    tc = make_tool_call("call-2", "completely_nonexistent_thing")

    result = planner_with_browser_click.plan([tc])

    assert result.plans == []
    assert len(result.early_messages) == 1
    assert "not found" in result.early_messages[0]["content"]


def test_planner_strict_match_skips_repair(planner_with_browser_click, caplog):
    tc = make_tool_call("call-3", "browser_click")

    with caplog.at_level(logging.WARNING, logger="test.name_planner"):
        result = planner_with_browser_click.plan([tc])

    assert len(result.plans) == 1
    assert not any("repaired to" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Codex P2 fix: ToolRunner.normalize_tool_calls
#   The repaired name (and surrogate-cleaned id/args) must reach BOTH the
#   history serializer and the runner. The unified normalize_tool_calls
#   returns a cleaned list that both consumers iterate, with proxy fallback
#   for frozen SDK objects.
# ---------------------------------------------------------------------------


@pytest.fixture
def runner_with_browser_click(tmp_path):
    from agentao.runtime.tool_runner import ToolRunner
    from agentao.transport import NullTransport

    registry = ToolRegistry()
    registry.register(FakeNamedTool("browser_click"))
    engine = PermissionEngine(project_root=tmp_path)
    transport = NullTransport()
    logger = logging.getLogger("test.runner_name_repair")
    return ToolRunner(registry, engine, transport, logger)


class TestRunnerNormalizeToolCalls:
    def test_camel_case_repaired(self, runner_with_browser_click, caplog):
        tcs = [make_tool_call("c-1", "BrowserClick_tool")]
        with caplog.at_level(logging.WARNING, logger="test.runner_name_repair"):
            cleaned, changed = runner_with_browser_click.normalize_tool_calls(tcs)
        assert changed is True
        assert cleaned[0].function.name == "browser_click"
        assert any("repaired to" in r.getMessage() for r in caplog.records)

    def test_wire_invalid_space_in_name_repaired(self, runner_with_browser_click):
        tcs = [make_tool_call("c-2", "browser click")]
        cleaned, _ = runner_with_browser_click.normalize_tool_calls(tcs)
        assert cleaned[0].function.name == "browser_click"

    def test_already_valid_name_untouched(self, runner_with_browser_click):
        tcs = [make_tool_call("c-3", "browser_click")]
        cleaned, changed = runner_with_browser_click.normalize_tool_calls(tcs)
        assert changed is False
        assert cleaned[0] is tcs[0]
        assert cleaned[0].function.name == "browser_click"

    def test_unrepairable_name_untouched(self, runner_with_browser_click):
        tcs = [make_tool_call("c-4", "completely_unknown_tool")]
        cleaned, changed = runner_with_browser_click.normalize_tool_calls(tcs)
        assert changed is False
        assert cleaned[0].function.name == "completely_unknown_tool"

    def test_empty_input(self, runner_with_browser_click):
        out, changed = runner_with_browser_click.normalize_tool_calls([])
        assert out == [] and changed is False
        out, changed = runner_with_browser_click.normalize_tool_calls(None)
        assert out == [] and changed is False

    def test_round_trip_history_and_result_names_match(
        self, runner_with_browser_click,
    ):
        # End-to-end: the cleaned list reaches both the history serializer
        # and the planner — names match across the round trip.
        from agentao.runtime.chat_loop import _serialize_tool_call

        tcs = [make_tool_call("c-5", "BrowserClick_tool")]
        cleaned, _ = runner_with_browser_click.normalize_tool_calls(tcs)

        history_dict = _serialize_tool_call(cleaned[0])
        assert history_dict["function"]["name"] == "browser_click"

        result = runner_with_browser_click._planner.plan(cleaned)
        assert len(result.plans) == 1
        assert result.plans[0].function_name == "browser_click"
