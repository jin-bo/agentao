"""Claude Code Stop exit-code-2 contract.

Phase B's Stop-specific runner forks from ``_run_command_hook`` so it
can honor exit code 2 as a force-continue signal — the previous draft
silently demoted exit-2 to a benign warning attachment. This test
prevents that regression.

Exit code 2 is checked BEFORE the JSON parser runs, so ``continue:
false`` in stdout cannot countermand it (Claude Code documents the
same precedence). Real subprocess required — the exit-code branch
lives in ``_run_stop_command_hook``, not the parser.
"""

from __future__ import annotations

from agentao.plugins.hooks import (
    ClaudeHookPayloadAdapter,
    PluginHookDispatcher,
)
from agentao.plugins.models import ParsedHookRule

from tests.support.stop_precompact import write_exit_code_hook


def _dispatch(tmp_path, *, exit_code: int, stderr: str):
    script = write_exit_code_hook(tmp_path, exit_code=exit_code, stderr=stderr)
    rule = ParsedHookRule(
        event="Stop", hook_type="command",
        command=f"sh '{script}'", plugin_name="t",
    )
    payload = ClaudeHookPayloadAdapter().build_stop(
        cwd=tmp_path,
        last_assistant_message="answer",
        turn_end_reason="final_response",
    )
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    return dispatcher.dispatch_stop(payload=payload, rules=[rule])


def test_exit_code_2_with_stderr_sets_force_continue(tmp_path):
    result = _dispatch(tmp_path, exit_code=2, stderr="please retry")

    assert result.force_continue is True
    assert result.follow_up_message == "please retry"
    assert result.stop_reason == "please retry"
    types = [m.attachment_type for m in result.messages]
    assert "hook_stop_blocked_via_exit2" in types


def test_exit_code_2_with_empty_stderr_uses_default_reason(tmp_path):
    result = _dispatch(tmp_path, exit_code=2, stderr="")

    assert result.force_continue is True
    # Every force_continue branch produces a non-empty follow_up_message.
    assert result.follow_up_message
