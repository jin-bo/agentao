from __future__ import annotations

import logging
from typing import Any, Dict

import pytest

from agentao.permissions import PermissionEngine
from agentao.runtime.name_repair import repair_tool_name
from agentao.runtime.tool_planning import (
    DOOM_LOOP_THRESHOLD,
    EMPTY_TOOL_NAME_MESSAGE,
    _EMPTY_NAME_PLACEHOLDER,
    ToolCallDecision,
    ToolCallPlanner,
)
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
    content = result.early_messages[0]["content"]
    assert "not found" in content
    # A genuinely-wrong but NON-empty name is a typo the model can correct,
    # so the full catalog (here: the registered tool name) is still dumped.
    assert "browser_click" in content
    assert content != EMPTY_TOOL_NAME_MESSAGE


# ---------------------------------------------------------------------------
# Empty/whitespace name anti-priming (hermes-agent 020e59d3c, #47967):
#   a blank name is never a fuzzy-repairable typo — it is a weak model
#   echoing tool-call syntax it saw as data. The guard is HOISTED above the
#   doom-loop and parse-failure checks so identical-args and malformed-args
#   echoes (the common forms) reach it instead of being pre-empted. Reply
#   with a terse note and WITHHOLD the tool catalog so we don't feed the
#   priming loop more names to mimic; a dedicated cumulative counter hard-
#   stops the turn if the echoes keep coming. Genuine (non-empty) typos still
#   get the catalog (see test_planner_unrepairable_name_still_emits_error).
# ---------------------------------------------------------------------------


def test_empty_tool_name_message_pins_intent():
    # Pin the constant's anti-priming contract independently of the runtime
    # branch, so re-introducing a catalog or dropping the 'data, do not
    # re-emit' guidance fails loudly here (the per-call tests assert equality
    # against this same symbol and so cannot catch its own degradation).
    assert "do not re-emit" in EMPTY_TOOL_NAME_MESSAGE
    assert "plain text" in EMPTY_TOOL_NAME_MESSAGE
    assert "Available tools" not in EMPTY_TOOL_NAME_MESSAGE
    assert "browser_click" not in EMPTY_TOOL_NAME_MESSAGE


class TestEmptyNameAntiPriming:
    @pytest.mark.parametrize("empty_name", ["", "   ", "\t", "\n ", " \t\n"])
    def test_single_empty_or_whitespace_name_withholds_catalog(
        self, planner_with_browser_click, empty_name, caplog,
    ):
        tc = make_tool_call("call-empty", empty_name, arguments="{}")

        with caplog.at_level(logging.WARNING, logger="test.name_planner"):
            result = planner_with_browser_click.plan([tc])

        assert result.plans == []
        assert result.doom_loop_triggered is False
        assert len(result.early_messages) == 1
        msg = result.early_messages[0]
        # The terse anti-priming reply, verbatim — no catalog, no "not found".
        assert msg["content"] == EMPTY_TOOL_NAME_MESSAGE
        assert "browser_click" not in msg["content"]
        assert "Available tools" not in msg["content"]
        assert "not found" not in msg["content"]
        # tool_call_id is answered (one reply per call). ``name`` is the
        # synthetic placeholder, never the empty/whitespace name itself, so a
        # strict provider that validates non-empty tool-message names accepts.
        assert msg["tool_call_id"]
        assert msg["role"] == "tool"
        assert msg["name"] == _EMPTY_NAME_PLACEHOLDER
        assert msg["name"].strip()
        # Logged as withheld-catalog, not as a raw KeyError dump.
        assert any("anti-priming" in r.getMessage() for r in caplog.records)

    def test_empty_name_with_unparseable_args_anti_primes(
        self, planner_with_browser_click,
    ):
        # Hoist regression: a blank-name echo whose args are ALSO malformed
        # must still get the anti-priming reply — not the parse-failure
        # handler's "retry with valid JSON", which would invite re-emission.
        tc = make_tool_call("call-bad", "", arguments="{not valid json")

        result = planner_with_browser_click.plan([tc])

        assert result.plans == []
        assert len(result.early_messages) == 1
        content = result.early_messages[0]["content"]
        assert content == EMPTY_TOOL_NAME_MESSAGE
        assert "valid JSON" not in content
        assert "could not parse" not in content

    def test_empty_name_identical_args_anti_primes_not_doom_abort(
        self, planner_with_browser_click,
    ):
        # Hoist regression: identical-args empty echoes used to trip the
        # (name, args_raw) doom-loop and abort the batch with a generic
        # doom message. Below threshold they now get the anti-priming reply.
        tcs = [
            make_tool_call("c-1", "", arguments="{}"),
            make_tool_call("c-2", "", arguments="{}"),
        ]

        result = planner_with_browser_click.plan(tcs)

        assert result.plans == []
        assert result.doom_loop_triggered is False
        assert len(result.early_messages) == 2
        assert all(
            m["content"] == EMPTY_TOOL_NAME_MESSAGE for m in result.early_messages
        )
        assert not any(
            "Doom-loop detected" in m["content"] for m in result.early_messages
        )

    @pytest.mark.parametrize(
        "args_seq",
        [
            ['{"i": 1}', '{"i": 2}', '{"i": 3}', '{"i": 4}'],  # varying args
            ["{}", "{}", "{}", "{}"],                          # identical args
        ],
    )
    def test_empty_name_flood_hard_stops_the_turn(
        self, planner_with_browser_click, args_seq,
    ):
        # The dedicated cumulative counter halts the turn at the threshold
        # regardless of whether the echoed args vary — the backstop the old
        # (name, args_raw) doom-loop could not provide for varying args.
        tcs = [make_tool_call(f"c-{i}", "", arguments=a)
               for i, a in enumerate(args_seq)]

        result = planner_with_browser_click.plan(tcs)

        assert result.plans == []
        assert result.doom_loop_triggered is True
        # Calls before the threshold get the terse reply; the threshold call
        # gets the stop message; calls after it are never processed.
        assert len(result.early_messages) == DOOM_LOOP_THRESHOLD
        assert result.early_messages[-1]["content"].startswith(
            EMPTY_TOOL_NAME_MESSAGE
        )
        assert "stopping to prevent a loop" in result.early_messages[-1]["content"]
        assert result.early_messages[-1]["name"] == _EMPTY_NAME_PLACEHOLDER

    def test_empty_name_counter_accumulates_across_batches_and_resets(
        self, planner_with_browser_click,
    ):
        # The counter is per-turn (cumulative across plan() calls), mirroring
        # the doom-loop counter, and clears on reset() between chat() turns.
        for _ in range(DOOM_LOOP_THRESHOLD - 1):
            r = planner_with_browser_click.plan(
                [make_tool_call("c", "", arguments="{}")]
            )
            assert r.doom_loop_triggered is False
        # The next empty call crosses the threshold and stops the turn.
        r = planner_with_browser_click.plan(
            [make_tool_call("c", "", arguments="{}")]
        )
        assert r.doom_loop_triggered is True

        planner_with_browser_click.reset()
        r = planner_with_browser_click.plan(
            [make_tool_call("c", "", arguments="{}")]
        )
        assert r.doom_loop_triggered is False
        assert r.early_messages[0]["content"] == EMPTY_TOOL_NAME_MESSAGE

    def test_empty_name_does_not_abort_legit_call_in_same_batch(
        self, planner_with_browser_click,
    ):
        # A single phantom empty call alongside a real tool must not halt the
        # batch — the real call is still planned.
        tcs = [
            make_tool_call("c-empty", "", arguments="{}"),
            make_tool_call("c-real", "browser_click"),
        ]

        result = planner_with_browser_click.plan(tcs)

        assert result.doom_loop_triggered is False
        assert len(result.plans) == 1
        assert result.plans[0].function_name == "browser_click"
        assert len(result.early_messages) == 1
        assert result.early_messages[0]["content"] == EMPTY_TOOL_NAME_MESSAGE


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
