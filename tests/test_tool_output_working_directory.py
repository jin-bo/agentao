"""Saved tool outputs live under the session's working directory, and age out.

The excerpt of an oversized tool result tells the model to ``read_file`` the
saved copy. ``read_file`` resolves a relative path against the session's
``working_directory``; the save used to resolve against the *process* cwd.
In the interactive CLI the two are the same directory, so nothing showed. An
ACP server or embedded host runs sessions whose working directory is not its
own, and there the model was handed a path its own ``read_file`` answered
"does not exist" for — while every session's output piled up in the host's
cwd, never deleted.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

from agentao.agent import Agentao
from agentao.runtime import tool_result_formatter as trf


def _plan(call_id="call_1", fn_name="run_shell_command"):
    from agentao.runtime.tool_planning import ToolCallDecision, ToolCallPlan
    from agentao.tools.base import Tool

    class _StubTool(Tool):
        name = fn_name
        description = "stub"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):  # pragma: no cover — never invoked here
            return ""

    return ToolCallPlan(
        tool_call=SimpleNamespace(id=call_id, function=SimpleNamespace(
            name=fn_name, arguments="{}")),
        function_name=fn_name,
        function_args={},
        tool=_StubTool(),
        decision=ToolCallDecision.ALLOW,
        tool_call_id=call_id,
    )


def _exec_result(result, fn_name="run_shell_command"):
    from agentao.runtime.tool_executor import ToolExecutionResult

    return ToolExecutionResult(
        fn_name=fn_name, result=result, status="ok", duration_ms=1, error=None,
    )


def _big_output() -> str:
    return "first line\n" + ("filler\n" * 10_000) + "LAST LINE\n"


def _saved_path(content: str) -> str:
    match = re.search(r"Full output saved to: (\S+)", content)
    assert match, content[:300]
    return match.group(1)


def test_the_saved_copy_is_readable_by_the_sessions_own_read_file(tmp_path, monkeypatch):
    """The reproduction: process cwd A, session working directory B."""
    host_cwd = tmp_path / "host"
    project = tmp_path / "project"
    host_cwd.mkdir()
    project.mkdir()
    monkeypatch.chdir(host_cwd)

    agent = Agentao(
        working_directory=project,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        logger=logging.getLogger("test.tool_output_wd"),
    )
    try:
        message = agent.tool_runner._formatter._format_one(
            _plan(), _exec_result(_big_output()),
        )
        path = _saved_path(message["content"])

        read = agent.tools.get("read_file").execute(file_path=path)
        assert "does not exist" not in read
        assert "first line" in read

        assert Path(path).is_relative_to(project.resolve())
        assert not (host_cwd / ".agentao").exists()
    finally:
        agent.close()


def test_old_outputs_are_pruned_by_mtime_and_recent_ones_kept(tmp_path):
    out_dir = tmp_path / ".agentao" / "tool-outputs"
    out_dir.mkdir(parents=True)
    now = time.time()
    # Names carry a *fresh* timestamp on both files: age is read from the
    # mtime, never from the name.
    old = out_dir / f"run_shell_command_{int(now)}_aaaaaa.txt"
    recent = out_dir / f"run_shell_command_{int(now)}_bbbbbb.txt"
    unrelated = out_dir / "notes.md"
    for f in (old, recent, unrelated):
        f.write_text("x", encoding="utf-8")
    ten_days, three_days = now - 10 * 86400, now - 3 * 86400
    os.utime(old, (ten_days, ten_days))
    os.utime(recent, (three_days, three_days))
    os.utime(unrelated, (ten_days, ten_days))

    assert trf._prune_tool_outputs(out_dir, now=now) == 1
    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()


def test_the_first_spill_prunes_and_later_spills_do_not(tmp_path, monkeypatch):
    out_dir = tmp_path / ".agentao" / "tool-outputs"
    out_dir.mkdir(parents=True)
    stale = out_dir / "run_shell_command_1_cccccc.txt"
    stale.write_text("x", encoding="utf-8")
    long_ago = time.time() - 30 * 86400
    os.utime(stale, (long_ago, long_ago))

    calls = []
    real_prune = trf._prune_tool_outputs
    monkeypatch.setattr(
        trf, "_prune_tool_outputs",
        lambda d, logger=None: calls.append(d) or real_prune(d, logger),
    )

    class _Transport:
        def emit(self, _event):
            pass

    formatter = trf.ToolResultFormatter(
        _Transport(), logging.getLogger("t"), working_directory=tmp_path,
    )
    formatter._format_one(_plan("c1"), _exec_result(_big_output()))
    formatter._format_one(_plan("c2"), _exec_result(_big_output()))

    assert calls == [out_dir]
    assert not stale.exists()
    assert len(list(out_dir.glob("*.txt"))) == 2


def test_a_prune_failure_never_costs_the_result(tmp_path, monkeypatch):
    """Pruning is best-effort: a file that cannot be stat'd or removed is skipped."""
    out_dir = tmp_path / "tool-outputs"
    out_dir.mkdir()
    victim = out_dir / "run_shell_command_1_dddddd.txt"
    victim.write_text("x", encoding="utf-8")
    long_ago = time.time() - 30 * 86400
    os.utime(victim, (long_ago, long_ago))

    def _refuse(self, *a, **k):
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "unlink", _refuse)
    assert trf._prune_tool_outputs(out_dir) == 0
    assert victim.exists()
