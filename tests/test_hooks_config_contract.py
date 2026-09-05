"""The configuration contract — step 3 of the conformance plan.

The deviation this closes is the one upstream of the other nine: a `hooks.json`
copied out of a Claude Code setup parsed to **zero rules**, because agentao read
the matcher *group* as a handler. The comparison measured what a hook receives on
stdin and what it may print, and never asked whether the hook is registered at
all.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from agentao.plugins.hooks import ClaudeHooksParser, PluginHookDispatcher
from agentao.plugins.hooks._paths import _placeholder_values, _substitute, plugin_data_dir
from agentao.plugins.hooks._profile import LEGACY_CONTRACT_ID, PROFILE_ID
from agentao.plugins.models import ParsedHookRule


def parse(raw, **kw):
    return ClaudeHooksParser().parse_dict(raw, plugin_name="p", **kw)


def official(matcher="Bash", **handler):
    h = {"type": "command", "command": "x"}
    h.update(handler)
    entry = {"hooks": [h]}
    if matcher is not None:
        entry["matcher"] = matcher
    return {"hooks": {"PreToolUse": [entry]}}


# --------------------------------------------------------------------------
# Shape detection — the copied file must work
# --------------------------------------------------------------------------

def test_the_official_shape_with_no_contract_key_parses():
    """The whole point: a copied Claude file has no `contract` key, so gating
    official-shape parsing on one leaves it at zero rules."""
    rules, warnings = parse(official())
    assert len(rules) == 1
    assert rules[0].contract == PROFILE_ID
    assert rules[0].matcher_pattern == "Bash"
    assert rules[0].command == "x"
    assert warnings == []


def test_the_flat_shape_is_unchanged():
    rules, warnings = parse({"hooks": {"PreToolUse": [
        {"type": "command", "command": "x", "matcher": {"toolName": "Bash"}}]}})
    assert len(rules) == 1
    assert rules[0].contract == LEGACY_CONTRACT_ID
    assert rules[0].matcher == {"toolName": "Bash"}
    assert rules[0].timeout == 60          # v1's default, frozen
    assert warnings == []


def test_a_matcher_group_with_several_handlers_yields_several_rules():
    rules, _ = parse({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "a"},
                   {"type": "command", "command": "b"}]}]}})
    assert [r.command for r in rules] == ["a", "b"]


def test_a_bare_events_dict_is_still_shape_detected():
    """The bare form has nowhere to put `contract`, which is exactly why
    detection cannot depend on it."""
    rules, _ = parse({"PreToolUse": [{"matcher": "Bash",
                                      "hooks": [{"type": "command", "command": "x"}]}]})
    assert len(rules) == 1 and rules[0].contract == PROFILE_ID


# --------------------------------------------------------------------------
# Every failure is file-level
# --------------------------------------------------------------------------

def test_a_mixed_file_is_disabled_whole():
    """Half a hook configuration is not a configuration."""
    rules, warnings = parse({"hooks": {"PreToolUse": [
        {"matcher": "B", "hooks": [{"type": "command", "command": "x"}]},
        {"type": "command", "command": "y"},
    ]}})
    assert rules == []
    assert any("mixes" in w.message for w in warnings)


def test_an_entry_claiming_both_shapes_disables_the_file():
    """*Both* keys is a contradiction: the entry claims to be both contracts."""
    rules, warnings = parse({"hooks": {"PreToolUse": [
        {"type": "command", "command": "x", "hooks": [{"type": "command"}]}]}})
    assert rules == []
    assert any("both shapes" in w.message for w in warnings)


def test_an_entry_claiming_neither_shape_does_not_disable_a_v1_file():
    """*Neither* key is a malformed handler, not a shape conflict — and
    `agentao-v1` is frozen, so its siblings must keep working exactly as they did
    before this parser was rewritten."""
    rules, warnings = parse({"hooks": {"Stop": [
        {"type": "command", "command": "echo ok"},
        {"command": "echo forgot-type"},
    ]}})
    assert [r.command for r in rules] == ["echo ok"]
    assert any("Unknown hook type" in w.message for w in warnings)
    assert not any("disabled" in w.message for w in warnings)


def test_a_matcher_group_with_no_handlers_is_reported():
    rules, warnings = parse({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": []},
        {"matcher": "Read", "hooks": [{"type": "command", "command": "x"}]},
    ]}})
    assert [r.matcher_pattern for r in rules] == ["Read"]
    assert any("no 'hooks' list" in w.message for w in warnings)


def test_an_unsupported_event_does_not_get_a_vote_on_the_file_shape():
    """A copied Claude config routinely carries events outside the profile — the
    reference documents 56 — and one of them must not disable the eight that
    work."""
    rules, warnings = parse({"hooks": {
        "Notification": [{"matcher": "*", "hooks": [{"type": "command", "command": "n"}]}],
        "Stop": [{"type": "command", "command": "s"}],
    }})
    assert [r.command for r in rules] == ["s"]
    assert any("Unsupported hook event" in w.message for w in warnings)
    assert not any("mixes" in w.message for w in warnings)


def test_an_unknown_contract_disables_the_file_rather_than_falling_back():
    """Falling back to the frozen contract would run the author's hooks under
    semantics they did not ask for — a silent misinterpretation."""
    raw = official()
    raw["contract"] = "claude-code@profile-99"
    rules, warnings = parse(raw)
    assert rules == []
    assert any("Unknown hook contract" in w.message for w in warnings)


def test_a_contract_that_disagrees_with_the_shape_is_a_rejection():
    raw = official()
    raw["contract"] = LEGACY_CONTRACT_ID
    rules, warnings = parse(raw)
    assert rules == []
    assert any("declares contract" in w.message for w in warnings)


def test_the_alias_resolves_to_the_newest_profile():
    raw = official()
    raw["contract"] = "claude-code"
    rules, _ = parse(raw)
    assert rules[0].contract == PROFILE_ID


def test_an_explicit_contract_agreeing_with_the_shape_is_kept():
    raw = official()
    raw["contract"] = PROFILE_ID
    rules, warnings = parse(raw)
    assert len(rules) == 1 and warnings == []


# --------------------------------------------------------------------------
# The handler-field matrix (§2.4)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hook_type", ["prompt", "http", "agent", "mcp_tool"])
def test_non_command_handler_types_are_rejected_with_a_reason(hook_type):
    rules, warnings = parse(official(type=hook_type))
    assert rules == []
    assert any(hook_type in w.message for w in warnings)


def test_the_prompt_rejection_says_why_it_is_a_different_feature():
    _, warnings = parse(official(type="prompt"))
    assert any("injects the template as context" in w.message for w in warnings)


@pytest.mark.parametrize("field", ["async", "asyncRewake", "if"])
def test_unimplemented_handler_fields_reject_the_rule(field):
    rules, warnings = parse(official(**{field: True}))
    assert rules == []
    assert any(field in w.message for w in warnings)


def test_shell_is_ignored_with_a_diagnostic_not_rejected():
    """Measured: upstream ignores it too. Rejecting the rule would disable a hook
    that runs on Claude Code — a conformance regression in the direction the
    profile exists to prevent."""
    rules, warnings = parse(official(shell="bash"))
    assert len(rules) == 1
    assert any("'shell'" in w.message and "no effect" in w.message for w in warnings)


@pytest.mark.parametrize("field,value", [("statusMessage", "linting…"), ("once", True)])
def test_cosmetic_fields_are_ignored_without_noise(field, value):
    rules, warnings = parse(official(**{field: value}))
    assert len(rules) == 1
    assert warnings == []


def test_profile_timeout_defaults_follow_the_reference():
    rules, _ = parse(official())
    assert rules[0].timeout == 600
    ups, _ = parse({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "x"}]}]}})
    assert ups[0].timeout == 30            # the user is waiting on this one
    explicit, _ = parse(official(timeout=5))
    assert explicit[0].timeout == 5


def test_args_is_accepted_and_type_checked():
    rules, _ = parse(official(args=["--flag", "${CLAUDE_PLUGIN_ROOT}/x"]))
    assert rules[0].args == ["--flag", "${CLAUDE_PLUGIN_ROOT}/x"]
    bad, warnings = parse(official(args="not-a-list"))
    assert bad == []
    assert any("'args'" in w.message for w in warnings)


def test_a_dict_matcher_is_refused_in_profile_mode():
    rules, warnings = parse({"hooks": {"PreToolUse": [
        {"matcher": {"toolName": "Bash"}, "hooks": [{"type": "command", "command": "x"}]}]}})
    assert rules == []
    assert any("must be a string" in w.message for w in warnings)


# --------------------------------------------------------------------------
# Matcher evaluation — pinned to the probe, not to the prose
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pattern,fires", [
    ("*", True),
    ("Read", True),
    ("^Read$", True),
    ("Read|Write", True),
    ("Rea.*", True),
    ("ead", False),          # refutes an unanchored search
    ("Rea|Wri", False),      # refutes exact-alternation-of-prefixes too
])
def test_profile_matcher_matches_what_claude_code_2_1_251_matched(pattern, fires):
    dispatcher = PluginHookDispatcher()
    rule = ParsedHookRule(event="PreToolUse", hook_type="command", command="x",
                          contract=PROFILE_ID, matcher_pattern=pattern)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Read"}
    assert dispatcher._matches(rule, payload) is fires


def test_a_profile_rule_with_no_matcher_fires_for_everything():
    dispatcher = PluginHookDispatcher()
    rule = ParsedHookRule(event="PreToolUse", hook_type="command", command="x",
                          contract=PROFILE_ID)
    assert dispatcher._matches(rule, {"tool_name": "Anything"}) is True


@pytest.mark.parametrize("event,field,value,pattern,fires", [
    # Measured against claude 2.1.251 (probe §G6): a SessionStart matcher is
    # compared against `source`, a SessionEnd matcher against `reason`.
    ("SessionStart", "source", "startup", "startup", True),
    ("SessionStart", "source", "startup", "resume", False),
    ("SessionStart", "source", "startup", "*", True),
    ("SessionEnd", "reason", "other", "other", True),
    ("SessionEnd", "reason", "other", "clear", False),
])
def test_session_events_match_on_the_field_upstream_compares(event, field, value, pattern, fires):
    """Returning "" for these events made every non-`*` matcher on them silently
    dead — a rule the parser accepted and nothing ever fired."""
    dispatcher = PluginHookDispatcher()
    rule = ParsedHookRule(event=event, hook_type="command", command="x",
                          contract=PROFILE_ID, matcher_pattern=pattern)
    payload = {"hook_event_name": event, field: value}
    assert dispatcher._matches(rule, payload) is fires


def test_precompact_matches_on_its_trigger():
    dispatcher = PluginHookDispatcher()
    rule = ParsedHookRule(event="PreCompact", hook_type="command", command="x",
                          contract=PROFILE_ID, matcher_pattern="manual")
    assert dispatcher._matches(rule, {"hook_event_name": "PreCompact", "trigger": "manual"})
    assert not dispatcher._matches(rule, {"hook_event_name": "PreCompact", "trigger": "auto"})


def test_the_dict_matcher_path_is_untouched_for_v1_rules():
    dispatcher = PluginHookDispatcher()
    rule = ParsedHookRule(event="PreToolUse", hook_type="command", command="x",
                          matcher={"toolName": "Read"}, contract=LEGACY_CONTRACT_ID)
    assert dispatcher._matches(rule, {"data": {"toolName": "Read"}}) is True
    assert dispatcher._matches(rule, {"data": {"toolName": "Write"}}) is False


# --------------------------------------------------------------------------
# The three path placeholders (§2.4, §7.1)
# --------------------------------------------------------------------------

def test_there_are_three_placeholders_not_two():
    """The data directory is the one that gets forgotten."""
    rule = ParsedHookRule(event="Stop", hook_type="command", command="x",
                          plugin_name="myplugin", plugin_root="/plugins/myplugin")
    values = _placeholder_values(rule, "/proj")
    assert set(values) == {"CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"}
    assert values["CLAUDE_PROJECT_DIR"] == "/proj"
    assert values["CLAUDE_PLUGIN_ROOT"] == "/plugins/myplugin"
    assert values["CLAUDE_PLUGIN_DATA"] == str(plugin_data_dir("myplugin"))


def test_substitution_only_touches_the_three_known_names():
    """`expandvars` would rewrite `$HOME` and any `${OTHER}` the author meant the
    shell to see — silently, and differently from how the shell would."""
    values = {"CLAUDE_PROJECT_DIR": "/proj", "CLAUDE_PLUGIN_ROOT": "/r", "CLAUDE_PLUGIN_DATA": "/d"}
    out = _substitute("cd ${CLAUDE_PROJECT_DIR} && echo $HOME ${OTHER}", values)
    assert out == "cd /proj && echo $HOME ${OTHER}"


def test_the_plugin_data_dir_is_not_inside_the_plugin_tree():
    """A marketplace-installed plugin directory may be read-only."""
    assert "plugin-data" in str(plugin_data_dir("p"))
    assert str(plugin_data_dir("p")).startswith(str(os.path.expanduser("~")))


def test_placeholders_reach_the_child_and_the_credential_scrub_survives(tmp_path, monkeypatch):
    """§10 item 1, the lead most at risk from this step: writing the export as
    `env={...}` would silently delete the provider-key scrub."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-reach-the-hook")
    out = tmp_path / "env.txt"
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    # The child reads its own environment rather than asking a shell to expand it: the
    # claim is that the placeholders arrive and the provider key does not, and ``cmd.exe``
    # expands neither ``$VAR`` nor ``${VAR:-default}``. The exec form still goes through
    # agentao's own substitution, one argument at a time.
    rule = ParsedHookRule(
        event="Stop", hook_type="command",
        command=sys.executable,
        args=[
            "-c",
            "import os, sys; open(sys.argv[1], 'w', encoding='utf-8').write("
            "'root=' + os.environ.get('CLAUDE_PLUGIN_ROOT', '') + "
            "' key=[' + os.environ.get('OPENAI_API_KEY', 'ABSENT') + ']')",
            str(out),
        ],
        contract=PROFILE_ID, plugin_name="p", plugin_root=str(tmp_path / "plug"), timeout=30,
    )

    proc, failure = dispatcher._run_subprocess(rule, {"hook_event_name": "Stop"})

    assert failure is None and proc is not None and proc.returncode == 0
    written = out.read_text(encoding="utf-8")
    assert f"root={tmp_path / 'plug'}" in written
    assert "key=[ABSENT]" in written


def test_placeholders_are_substituted_into_the_command_text(tmp_path):
    out = tmp_path / "sub.txt"
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    # ``${CLAUDE_PROJECT_DIR}`` is substituted by agentao into the command *text*, which is
    # what this pins. Reading it back through ``echo`` measured the shell's quoting instead:
    # cmd writes the quotes it was given.
    rule = ParsedHookRule(
        event="Stop", hook_type="command",
        command=sys.executable,
        args=["-c", "import sys; open(sys.argv[2], 'w', encoding='utf-8').write(sys.argv[1])",
              "${CLAUDE_PROJECT_DIR}", str(out)],
        contract=PROFILE_ID, plugin_name="p", timeout=30,
    )
    dispatcher._run_subprocess(rule, {"hook_event_name": "Stop"})
    assert out.read_text(encoding="utf-8").strip() == str(tmp_path)


def test_exec_form_runs_without_a_shell(tmp_path):
    """`args` present means no shell, so shell metacharacters stay literal —
    which is the reason the reference tells authors to use it with paths."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    # ``echo`` is a shell builtin, not a program, so the exec form cannot run it on Windows.
    # The interpreter running this test is a program everywhere, and printing its arguments
    # is the same measurement: the metacharacters have to arrive literal.
    rule = ParsedHookRule(
        event="Stop", hook_type="command", command=sys.executable,
        args=["-c", "import sys; print(' '.join(sys.argv[1:]))",
              "a b; echo injected", "${CLAUDE_PLUGIN_ROOT}"],
        contract=PROFILE_ID, plugin_name="p", plugin_root="/root/of/plugin", timeout=30,
    )

    proc, failure = dispatcher._run_subprocess(rule, {"hook_event_name": "Stop"})

    assert failure is None and proc is not None
    assert proc.stdout.strip() == "a b; echo injected /root/of/plugin"
    assert "injected\n" not in proc.stdout.replace("a b; echo injected", "")


def test_a_profile_rule_receives_the_profile_shape_on_stdin(tmp_path):
    """One rule, one contract, one wire shape — never both in one payload."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(event="Stop", hook_type="command", command="cat",
                          contract=PROFILE_ID, plugin_name="p", timeout=30)
    proc, _ = dispatcher._run_subprocess(
        rule, {"hook_event_name": "Stop", "session_id": "s", "cwd": "/proj",
               "last_assistant_message": "hi", "turn_end_reason": "final_response"},
    )
    sent = json.loads(proc.stdout)
    assert sent["hook_event_name"] == "Stop"
    assert sent["last_assistant_message"] == "hi"
    assert "transcript_path" in sent and sent["transcript_path"] is None
    # agentao's own field is forbidden on this event and must not ride along.
    assert "turn_end_reason" not in sent


def test_a_v1_rule_still_receives_todays_envelope(tmp_path):
    """`agentao-v1` is frozen: the same dispatch must hand it the old shape."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(event="Stop", hook_type="command", command="cat",
                          contract=LEGACY_CONTRACT_ID, plugin_name="p", timeout=30)
    proc, _ = dispatcher._run_subprocess(
        rule, {"hook_event_name": "Stop", "k": "v", "turn_end_reason": "final_response"},
    )
    sent = json.loads(proc.stdout)
    assert sent["k"] == "v"
    assert sent["turn_end_reason"] == "final_response"
