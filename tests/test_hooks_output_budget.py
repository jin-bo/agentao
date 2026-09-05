"""Hook output budget — the two tiers of the conformance plan's §6.

Tier 1 is a *memory* bound at the subprocess boundary; tier 2 is a *context*
bound on the strings a hook contributes. The pair is the point: capping only
the parsed strings leaves ``communicate()`` reading an unbounded pipe, and
capping only the raw bytes leaves 8 MiB of well-formed ``additionalContext``
going straight into the model's window.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentao.capabilities.process import OutputLimitExceeded, run_captured
from agentao.plugins.hooks import PluginHookDispatcher
from agentao.plugins.hooks import _budget, _dispatcher as dispatcher_mod
from agentao.plugins.hooks._budget import HOOK_CHANNEL_CHAR_LIMIT, cap_channel
from agentao.plugins.models import ParsedHookRule, StopHookResult, UserPromptSubmitResult

from ._hook_commands import as_kwargs


def _floods(stream: str) -> list:
    """A child that writes without stopping, on any platform.

    ``yes`` is a coreutils program; Windows has neither it nor a shell that would run the
    redirect around it. What the limit is being measured against is an unbounded writer, and
    a Python loop is one everywhere.
    """
    return [
        sys.executable, "-c",
        "import sys\nwhile True:\n    sys.%s.write('A' * 4096)" % stream,
    ]


# --------------------------------------------------------------------------
# Tier 1 — the raw ceiling
# --------------------------------------------------------------------------

def test_bounded_run_kills_a_flooding_child():
    """``yes`` never stops; the bound is what makes this test terminate."""
    with pytest.raises(OutputLimitExceeded) as excinfo:
        run_captured(_floods("stdout"), max_output_bytes=200_000)
    assert excinfo.value.stream == "stdout"
    assert excinfo.value.limit == 200_000


def test_bounded_run_catches_a_flood_on_stderr_too():
    with pytest.raises(OutputLimitExceeded) as excinfo:
        run_captured(_floods("stderr"), max_output_bytes=200_000)
    assert excinfo.value.stream == "stderr"


def test_the_limit_is_opt_in_so_other_callers_are_unchanged():
    """``search_file_content`` and every other caller must keep their output.

    The bound changes the failure mode — a kill instead of a result — so it
    cannot be a default.
    """
    proc = run_captured("head -c 400000 /dev/zero | tr '\\0' 'A'", shell=True)
    assert len(proc.stdout) == 400_000
    assert proc.returncode == 0


def test_bounded_run_preserves_the_ordinary_contract():
    """Same result shape, stdin still fed, exit code still reported.

    The child echoes stdin and exits 3. It used to be ``cat; exit 3`` under a shell,
    which is two things Windows does not have; what is under test is ``run_captured``,
    not the shell, so the child is a Python one on both platforms.
    """
    proc = run_captured(
        [sys.executable, "-c",
         "import sys; sys.stdout.write(sys.stdin.read()); sys.exit(3)"],
        input="fed on stdin", max_output_bytes=1_000_000,
    )
    assert proc.stdout == "fed on stdin"
    assert proc.returncode == 3


def test_bounded_run_still_raises_timeout_expired():
    """The bounded path must not swallow the failure mode callers already handle."""
    with pytest.raises(subprocess.TimeoutExpired):
        run_captured("sleep 30", shell=True, timeout=1, max_output_bytes=1_000_000)


def test_a_budget_kill_is_reported_differently_from_a_timeout(monkeypatch, tmp_path):
    """A hook killed for flooding must not be reported as "timed out"."""
    monkeypatch.setattr(dispatcher_mod, "HOOK_RAW_OUTPUT_LIMIT_BYTES", 100_000)
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(event="Stop", hook_type="command",
                          **as_kwargs((sys.executable, ["-c", "import sys\nwhile True:\n    sys.stdout.write('A' * 4096)"])),
                          timeout=30, plugin_name="t")

    proc, failure = dispatcher._run_subprocess(rule, {"event": "Stop"})

    assert proc is None
    assert failure == "output_budget"
    attachment = dispatcher._timeout_attachment(rule, failure)
    assert "output exceeded" in attachment.payload["warning"]
    assert "timed out" not in attachment.payload["warning"]


# --------------------------------------------------------------------------
# Tier 2 — the channel cap and its spill
# --------------------------------------------------------------------------

def test_a_short_channel_is_returned_unchanged_and_identical():
    """Identity, not just equality: callers use it to detect that nothing happened."""
    text = "a short reason"
    capped, diagnostic = cap_channel(text, hook_event="Stop")
    assert capped is text
    assert diagnostic is None


def test_an_over_budget_channel_is_excerpted_and_spilled(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    payload = "S" * 50_000

    capped, diagnostic = cap_channel(payload, hook_event="PostToolUse")

    assert len(capped) < len(payload)
    assert "50,000 chars" in capped
    assert diagnostic and "channel budget" in diagnostic
    # Head and tail both survive — the reason a hook prints tends to be at the end.
    assert capped.startswith("[Hook output truncated")
    assert "chars omitted" in capped

    spilled = list((tmp_path / ".agentao" / "hook-outputs").iterdir())
    assert len(spilled) == 1
    assert spilled[0].read_text() == payload


@pytest.mark.skipif(
    os.name == "nt",
    reason="0600 is unenforceable on Windows — see the docstring; the gap is real, not skipped away",
)
def test_the_spill_file_is_0600(monkeypatch, tmp_path):
    """Hook output is a user script's output — likelier to carry a credential
    than a tool result, which is why this sink tightens what tool-outputs does.

    **The guarantee does not exist on Windows, and this skip records that rather
    than papering over it.** ``os.open(..., 0o600)`` there sets only the read-only
    bit; access is decided by the file's ACL, which NTFS inherits from the parent
    directory. The spill lands in the *project* tree (``.agentao/hook-outputs``),
    so on a machine where the repository is readable by more than one account, hook
    output that may carry a credential is readable with it. Closing that needs a
    real ACL, which is the same unimplemented Windows identity work that
    ``agentao/paths.py`` and ``permissions_hardline/_trust.py`` both defer to.
    """
    monkeypatch.chdir(tmp_path)
    cap_channel("S" * 30_000, hook_event="Stop")
    spilled = next((tmp_path / ".agentao" / "hook-outputs").iterdir())
    assert oct(os.stat(spilled).st_mode & 0o777) == "0o600"


def test_credentials_are_redacted_before_the_bytes_land(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # A hook echoing its environment on error is the case this guards, so the
    # fixture is shaped like that rather than hand-built: the scanner anchors on
    # a word boundary, and a token spliced mid-word is not one a hook produces.
    secret = "sk-" + "a" * 48
    payload = "S" * 20_000 + f"\nOPENAI_API_KEY={secret}\n" + "S" * 20_000

    capped, _ = cap_channel(payload, hook_event="PreToolUse")

    on_disk = next((tmp_path / ".agentao" / "hook-outputs").iterdir()).read_text()
    assert secret not in on_disk
    assert "REDACTED" in on_disk
    # And the excerpt says so, because the model may be told to read the file.
    assert "REDACTED" in capped


def test_a_failed_spill_is_reported_and_the_excerpt_survives(monkeypatch, tmp_path):
    """§6.1: the existing tool sink swallows this. A pointer to a file that was
    never written is worse than saying the save failed."""
    monkeypatch.chdir(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(_budget.os, "open", boom)

    capped, diagnostic = cap_channel("S" * 30_000, hook_event="Stop")

    assert diagnostic and "spill failed" in diagnostic
    assert "disk full" in diagnostic
    assert "Full hook output saved to" not in capped
    assert len(capped) < 30_000       # the excerpt is still delivered
    assert "chars omitted" in capped


def test_the_spill_directory_is_pruned(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_budget, "_SPILL_MAX_FILES", 3)
    for _ in range(6):
        cap_channel("S" * 15_000, hook_event="Stop")
        time.sleep(0.01)   # distinct mtimes, so "newest" is well defined
    remaining = list((tmp_path / ".agentao" / "hook-outputs").iterdir())
    # Pruning runs before each write, so the cap holds within one file.
    assert len(remaining) <= 4


# --------------------------------------------------------------------------
# Tier 2 — wired into the parsers, per channel
# --------------------------------------------------------------------------

def _stop_result(payload: dict, tmp_path: Path) -> StopHookResult:
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(event="Stop", hook_type="command", command="x", plugin_name="t")
    result = StopHookResult(matched_rule_count=1)
    dispatcher._parse_stop_command_output(json.dumps(payload), rule, result)
    return result


def test_a_stop_reason_is_capped(monkeypatch, tmp_path):
    """A ``stopReason`` becomes the next turn's input, so it is a channel even
    though it is not one of the three fields the reference names."""
    monkeypatch.chdir(tmp_path)
    result = _stop_result({"stopReason": "R" * 40_000}, tmp_path)
    assert len(result.stop_reason) < 40_000
    assert "chars omitted" in result.stop_reason


def test_stop_additional_context_is_capped(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = _stop_result(
        {"hookSpecificOutput": {"additionalContext": "C" * 40_000}}, tmp_path,
    )
    assert len(result.additional_contexts[0]) < 40_000


def test_stop_system_message_is_capped(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = _stop_result({"systemMessage": "M" * 40_000}, tmp_path)
    assert len(result.system_message) < 40_000


def test_plain_non_json_stdout_is_capped(monkeypatch, tmp_path):
    """The plain-text-as-context path is the one a field-named cap misses."""
    monkeypatch.chdir(tmp_path)
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(event="UserPromptSubmit", hook_type="command",
                          command="x", plugin_name="t")
    result = UserPromptSubmitResult()
    dispatcher._parse_command_output("P" * 40_000, rule, result)
    assert len(result.additional_contexts[0]) < 40_000


def test_a_list_of_contexts_is_capped_per_item(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(event="UserPromptSubmit", hook_type="command",
                          command="x", plugin_name="t")
    result = UserPromptSubmitResult()
    dispatcher._parse_command_output(
        json.dumps({"additionalContext": ["A" * 30_000, "ok", "B" * 30_000]}),
        rule, result,
    )
    assert len(result.additional_contexts) == 3
    assert len(result.additional_contexts[0]) < 30_000
    assert result.additional_contexts[1] == "ok"
    assert len(result.additional_contexts[2]) < 30_000


def test_under_budget_output_is_untouched(monkeypatch, tmp_path):
    """The cap must be invisible to every hook that does not hit it — including
    leaving no spill directory behind."""
    monkeypatch.chdir(tmp_path)
    result = _stop_result({"systemMessage": "ran lint, all clean"}, tmp_path)
    assert result.system_message == "ran lint, all clean"
    assert not (tmp_path / ".agentao" / "hook-outputs").exists()


def test_the_limit_constant_matches_the_reference():
    """10,000 characters is upstream's number for hook output strings."""
    assert HOOK_CHANNEL_CHAR_LIMIT == 10_000
