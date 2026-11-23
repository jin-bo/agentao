"""``/sessions rename <name>`` (#663), on a real ``AgentaoCLI`` and real files.

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
    """What ``/sessions`` prints."""
    buf = io.StringIO()
    import agentao.cli.commands.sessions as sessions_mod
    monkeypatch.setattr(sessions_mod, "console", Console(file=buf, width=200, color_system=None))
    return buf


def _root(cli) -> Path:
    return Path(cli.agent.working_directory)


def _files(cli) -> List[dict]:
    d = _root(cli) / ".agentao" / "sessions"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] \
        if d.exists() else []


def _drive(cli, *steps) -> None:
    """Type each string step into ``run_loop``; run callables between prompts."""
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


def test_rename_is_saved_at_once_and_survives_turns_and_exit(cli):
    seen = {}

    def after_rename():
        # History did not change, yet the name is already on disk.
        files = _files(cli)
        assert len(files) == 1 and files[0]["name"] == "Payments refactor"
        seen["sid"] = files[0]["session_id"]

    _drive(cli, "first question", "/sessions rename Payments refactor",
           after_rename, "second question")

    files = _files(cli)
    assert len(files) == 1
    assert files[0]["session_id"] == seen["sid"]
    assert files[0]["name"] == "Payments refactor"
    assert files[0]["title"] == "first question"  # still derived
    [listed] = list_sessions(project_root=_root(cli))
    assert listed["title"] == "Payments refactor" and listed["name"] == "Payments refactor"


def test_resume_keeps_the_name_and_later_saves_keep_it(cli, out):
    _drive(cli, "hello", "/sessions rename Keep me", "/new", "other topic")
    named = next(f for f in _files(cli) if f.get("name"))
    unnamed = [f for f in _files(cli) if not f.get("name")]
    # ``/new`` starts a session without the old name.
    assert len(unnamed) == 1 and unnamed[0]["title"] == "other topic"

    _drive(cli, f"/sessions resume {named['session_id'][:8]}", "one more turn")

    assert "Resuming session" in out.getvalue() and "Keep me" in out.getvalue()
    files = [f for f in _files(cli) if f["session_id"] == named["session_id"]]
    assert len(files) == 1 and files[0]["name"] == "Keep me"
    assert len(files[0]["messages"]) == 4


def test_rename_before_any_message_is_saved_with_the_first(cli, out):
    _drive(cli, "/sessions rename Early", lambda: None)
    assert _files(cli) == []
    assert "saved with the first message" in out.getvalue()
    _drive(cli, "now a message")
    assert [f.get("name") for f in _files(cli)] == ["Early"]


def test_names_are_one_line_and_capped(cli, out):
    from agentao.cli.commands.sessions import _NAME_MAX_CHARS, _rename_session
    from agentao.cli.session import session_name

    _rename_session(cli, "  a\n\tb   c  ")
    assert session_name(cli) == "a b c"
    _rename_session(cli, "x" * 100)
    assert session_name(cli) == "x" * _NAME_MAX_CHARS
    assert f"cut to {_NAME_MAX_CHARS} characters" in out.getvalue()


def test_an_empty_name_is_refused(cli, out):
    from agentao.cli.commands.sessions import _rename_session
    from agentao.cli.session import session_name, set_session_name

    set_session_name(cli, "Before")
    _rename_session(cli, "   ")
    assert session_name(cli) == "Before"
    assert "Usage: /sessions rename <name>" in out.getvalue()


def test_names_print_literally_in_the_list(cli, out):
    name = "[bold red]x[/bold red] \x1b[2J end\\\\"
    _drive(cli, "hi", f"/sessions rename {name}", "/sessions list")
    text = out.getvalue()
    assert "\x1b" not in text
    # ESC is dropped; what followed it is plain text, as are the brackets and
    # the trailing backslashes.
    assert "    [bold red]x[/bold red] [2J end\\\\\n" in text


def test_a_file_without_a_name_keeps_its_title_fallback(tmp_path):
    msgs = [{"role": "user", "content": "old style"}, {"role": "assistant", "content": "ok"}]
    path, _sid = save_session(msgs, "m", project_root=tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "name" not in data
    # A pre-``title`` file falls back to the first user message.
    del data["title"]
    path.write_text(json.dumps(data), encoding="utf-8")
    [listed] = list_sessions(project_root=tmp_path)
    assert listed["title"] == "old style" and listed["name"] is None


def test_a_non_string_name_in_a_file_is_ignored(tmp_path):
    msgs = [{"role": "user", "content": "q"}]
    path, _sid = save_session(msgs, "m", project_root=tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["name"] = ["not", "a", "string"]
    path.write_text(json.dumps(data), encoding="utf-8")
    [listed] = list_sessions(project_root=tmp_path)
    assert listed["title"] == "q" and listed["name"] is None


def test_a_save_that_passes_no_name_keeps_the_sessions_name(tmp_path):
    """ACP's ``persist_agent_session`` passes neither ``name=`` nor
    ``supersedes``; the session's name must survive that save."""
    msgs = [{"role": "user", "content": "q"}]
    _p, sid = save_session(msgs, "m", project_root=tmp_path, name="Named")
    msgs.append({"role": "assistant", "content": "a"})
    save_session(msgs, "m", session_id=sid, project_root=tmp_path)
    files = sorted((tmp_path / ".agentao" / "sessions").glob("*.json"))
    newest = json.loads(files[-1].read_text(encoding="utf-8"))
    assert len(newest["messages"]) == 2 and newest["name"] == "Named"
    # Another session's name is not borrowed.
    _p, other = save_session(msgs, "m", project_root=tmp_path)
    by_sid = {s["session_id"]: s for s in list_sessions(project_root=tmp_path)}
    assert by_sid[other]["name"] is None


@pytest.mark.parametrize("bad", [123, "   ", ["x"]])
def test_save_session_writes_no_name_that_is_not_one(tmp_path, bad):
    path, _sid = save_session([{"role": "user", "content": "q"}], "m",
                              project_root=tmp_path, name=bad)
    assert "name" not in json.loads(path.read_text(encoding="utf-8"))


def test_every_whitespace_separates_words_and_a_cut_ends_on_no_space(cli, out):
    from agentao.cli.commands.sessions import _NAME_MAX_CHARS, _rename_session
    from agentao.cli.session import session_name

    _rename_session(cli, "foo\x0bbar\x0cbaz\rqux")
    assert session_name(cli) == "foo bar baz qux"
    _rename_session(cli, "w" * (_NAME_MAX_CHARS - 1) + " tail")
    assert session_name(cli) == "w" * (_NAME_MAX_CHARS - 1)


def test_a_hand_edited_name_is_tidied_in_the_list(tmp_path):
    msgs = [{"role": "user", "content": "derived"}]
    path, _sid = save_session(msgs, "m", project_root=tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for raw, shown in (("   ", "derived"), ("a\nb" + "c" * 200, "a b" + "c" * 57)):
        data["name"] = raw
        path.write_text(json.dumps(data), encoding="utf-8")
        [listed] = list_sessions(project_root=tmp_path)
        assert listed["title"] == shown


def test_a_failed_save_does_not_report_success(cli, out, monkeypatch):
    _drive(cli, "hello")
    import agentao.embedding.sessions as sessions_mod

    def boom(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(sessions_mod, "persist_agent_session", boom)
    from agentao.cli.commands.sessions import _rename_session
    from agentao.cli.session import session_name

    _rename_session(cli, "Unsaved")
    text = out.getvalue()
    assert "Saving it failed" in text and "Session renamed:" not in text
    assert session_name(cli) == "Unsaved"  # kept for the next save
