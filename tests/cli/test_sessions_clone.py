"""``/sessions clone [id]`` (#666), on a real ``AgentaoCLI`` and real files.

``agent.chat`` is a stub that appends to ``agent.messages``; saving reads
nothing else.
"""

from __future__ import annotations

import io
import json
from functools import partial
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest
from rich.console import Console

from agentao.embedding import build_from_environment, list_sessions, save_session
from agentao.embedding.sessions import _MAX_SESSIONS


@pytest.fixture
def cli(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    with patch("agentao.cli.app.safe_load_dotenv"), \
            patch("agentao.cli.subcommands._load_and_register_plugins"):
        from agentao.cli import AgentaoCLI
        cli = AgentaoCLI(agent_factory=partial(
            build_from_environment, working_directory=tmp_path / "proj"))

    def chat(message, images=None):
        cli.agent.messages.append({"role": "user", "content": message})
        cli.agent.messages.append({"role": "assistant", "content": f"re: {message}"})
        return f"re: {message}"
    cli.agent.chat = chat
    yield cli
    cli.agent.close()


@pytest.fixture
def out(monkeypatch):
    buf = io.StringIO()
    import agentao.cli.commands.sessions as sessions_mod
    monkeypatch.setattr(sessions_mod, "console", Console(file=buf, width=300, color_system=None))
    return buf


def _root(cli) -> Path:
    return Path(cli.agent.working_directory)


def _sdir(cli) -> Path:
    return _root(cli) / ".agentao" / "sessions"


def _files(cli) -> List[dict]:
    d = _sdir(cli)
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] \
        if d.exists() else []


def _drive(cli, *steps) -> None:
    queue = list(steps) + ["/exit"]

    def next_input():
        while True:
            step = queue.pop(0)
            if callable(step):
                step()
                continue
            return step
    cli._get_user_input = next_input
    from agentao.cli.input_loop import run_loop
    run_loop(cli)


def _hook_calls(monkeypatch) -> list:
    seen: list = []
    import agentao.cli.session as session_mod
    for name in ("_dispatch_session_end_hooks", "_dispatch_session_start_hooks"):
        real = getattr(session_mod, name)
        monkeypatch.setattr(session_mod, name,
                            lambda cli, _r=real, _n=name, **kw: (seen.append(_n), _r(cli, **kw)))
    return seen


def test_clone_current_stays_here_and_both_resume_on_their_own(cli, out, monkeypatch):
    state = {}

    def clone_now():
        state["sid"] = cli.current_session_id
        state["hooks"] = _hook_calls(monkeypatch)
        from agentao.cli.commands.sessions import _clone_session
        _clone_session(cli, "")
        assert cli.current_session_id == state["sid"]  # still here
        assert state["hooks"] == []  # a copy is not a session switch
        files = _files(cli)
        assert len(files) == 2
        copy = next(f for f in files if f["session_id"] != state["sid"])
        state["copy"] = copy["session_id"]
        assert [m["content"] for m in copy["messages"]] == ["first", "re: first"]

    _drive(cli, "first", clone_now, "second")

    by_sid = {f["session_id"]: f for f in _files(cli)}
    assert len(by_sid) == 2 and len(_files(cli)) == 2
    # The later turn went to the original's file, not the copy's.
    assert len(by_sid[state["sid"]]["messages"]) == 4
    assert len(by_sid[state["copy"]]["messages"]) == 2
    assert f"Resume the copy with /sessions resume {state['copy'][:8]}" in out.getvalue()

    _drive(cli, f"/sessions resume {state['copy'][:8]}", "on the copy")
    by_sid = {f["session_id"]: f for f in _files(cli)}
    assert len(by_sid[state["copy"]]["messages"]) == 4
    assert len(by_sid[state["sid"]]["messages"]) == 4  # untouched
    _drive(cli, f"/sessions resume {state['sid'][:8]}", "on the original")
    by_sid = {f["session_id"]: f for f in _files(cli)}
    assert len(by_sid[state["sid"]]["messages"]) == 6
    assert len(by_sid[state["copy"]]["messages"]) == 4


def test_clone_by_prefix_keeps_the_raw_record(cli, out):
    msgs = [
        {"role": "system", "content": "[Compact Boundary | tokens_before=9 | auto=True]"},
        {"role": "system", "content": "[Conversation Summary]\nSUMMARY"},
        {"role": "user", "content": "go on"},
        {"role": "assistant", "content": "ok"},
    ]
    _p, src = save_session(msgs, "src-model", ["skill-a"], session_id="abcd1234-src",
                           project_root=_root(cli), mcp_skill_origins=["server=docs"],
                           name="Payments")
    from agentao.cli.commands.sessions import _clone_session
    _clone_session(cli, "abcd")

    copy = next(f for f in _files(cli) if f["session_id"] != src)
    assert copy["messages"] == msgs  # no provenance record added
    assert copy["model"] == "src-model" and copy["active_skills"] == ["skill-a"]
    assert copy["mcp_skill_origins"] == ["server=docs"]
    assert copy["name"] == "Payments (copy)"
    assert copy["created_at"] >= json.loads(_p.read_text(encoding="utf-8"))["created_at"]
    assert src in {f["session_id"] for f in _files(cli)}  # the source is still there
    assert "Copied session abcd1234" in out.getvalue()


def test_a_long_name_is_cut_so_the_suffix_fits():
    from agentao.embedding.sessions import _TITLE_MAX_CHARS as _NAME_MAX_CHARS, _copy_name
    name = _copy_name("n" * 70)
    assert name.endswith(" (copy)") and len(name) <= _NAME_MAX_CHARS
    assert _copy_name(None) is None and _copy_name("  ") is None


def test_a_full_directory_refuses_and_changes_nothing(cli, out):
    for i in range(_MAX_SESSIONS):
        save_session([{"role": "user", "content": f"s{i}"}], "m",
                     session_id=f"sess-{i:02d}", project_root=_root(cli))
    before = {p.name: p.read_bytes() for p in _sdir(cli).glob("*.json")}
    from agentao.cli.commands.sessions import _clone_session
    _clone_session(cli, "sess-00")
    assert {p.name: p.read_bytes() for p in _sdir(cli).glob("*.json")} == before
    assert "Not copied" in out.getvalue() and "/sessions delete" in out.getvalue()


def test_the_last_free_slot_takes_a_copy_and_keeps_every_session(cli, out):
    for i in range(_MAX_SESSIONS - 1):
        save_session([{"role": "user", "content": f"s{i}"}], "m",
                     session_id=f"sess-{i:02d}", project_root=_root(cli))
    from agentao.cli.commands.sessions import _clone_session
    _clone_session(cli, "sess-00")
    sids = {s["session_id"] for s in list_sessions(project_root=_root(cli))}
    assert len(sids) == _MAX_SESSIONS and "sess-00" in sids


@pytest.mark.parametrize("arg, says", [
    ("", "Nothing to copy yet"),
    ("zzzz", "No saved session matches 'zzzz'"),
])
def test_nothing_to_copy(cli, out, arg, says):
    from agentao.cli.commands.sessions import _clone_session
    _clone_session(cli, arg)
    assert says in out.getvalue() and _files(cli) == []


def test_an_ambiguous_prefix_or_a_bad_file_is_refused(cli, out):
    save_session([{"role": "user", "content": "a"}], "m", session_id="abcd-1", project_root=_root(cli))
    save_session([{"role": "user", "content": "b"}], "m", session_id="abcd-2", project_root=_root(cli))
    from agentao.cli.commands.sessions import _clone_session
    _clone_session(cli, "abcd")
    assert "matches 2 sessions" in out.getvalue()

    path, _ = save_session([{"role": "user", "content": "c"}], "m", session_id="efgh-1",
                           project_root=_root(cli))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["messages"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    _clone_session(cli, "efgh")
    assert "no messages to copy" in out.getvalue()
    assert len(_files(cli)) == 3


def test_a_hand_edited_source_name_is_cleaned():
    from agentao.embedding.sessions import _copy_name
    assert _copy_name("\x1b[31mRed\x07") == "[31mRed (copy)"
    assert _copy_name("\x1b\x07") is None
