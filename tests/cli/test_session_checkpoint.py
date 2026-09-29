"""The CLI saves the conversation after every turn, not only at ``/exit``.

``on_session_end`` used to be the CLI's only save, so a closed terminal, a kill
or a crash lost the whole session. These tests drive the real ``run_loop`` on a
real ``AgentaoCLI`` and read ``.agentao/sessions`` *while the loop is still
running* — the state a killed process leaves behind.

``agent.chat`` is replaced by a stub that appends to ``agent.messages``: the
checkpoint reads nothing else, and a real LLM would add nothing here.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Callable, List, Union
from unittest.mock import patch

import pytest

from agentao.embedding import build_from_environment, save_session

Step = Union[str, Callable[[], None]]


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """A real CLI rooted in ``tmp_path``, with HOME redirected (``/clear``
    reaches the user memory store — see ``test_clear_resets_confirm.py``)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    with patch("agentao.cli.app.safe_load_dotenv"), \
            patch("agentao.cli.subcommands._load_and_register_plugins"):
        from agentao.cli import AgentaoCLI
        cli = AgentaoCLI(agent_factory=partial(
            build_from_environment, working_directory=tmp_path / "proj"))
    yield cli
    cli.agent.close()


def _sessions_dir(cli) -> Path:
    return Path(cli.agent.working_directory) / ".agentao" / "sessions"


def _files(cli) -> List[dict]:
    d = _sessions_dir(cli)
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] \
        if d.exists() else []


def _answering(cli, *, fail: BaseException = None):
    """``agent.chat`` stand-in: records the prompt, then answers or raises."""
    def chat(message, images=None):
        cli.agent.messages.append({"role": "user", "content": message})
        if fail is not None:
            raise fail
        cli.agent.messages.append({"role": "assistant", "content": f"re: {message}"})
        return f"re: {message}"
    cli.agent.chat = chat


def _drive(cli, *steps: Step) -> None:
    """Feed ``steps`` to ``run_loop``: a string is typed, a callable runs
    between prompts (where assertions about the disk go). Ends with ``/exit``."""
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


def _count_session_end(monkeypatch) -> list:
    seen: list = []
    import agentao.cli.session as session_mod
    real = session_mod._dispatch_session_end_hooks
    monkeypatch.setattr(session_mod, "_dispatch_session_end_hooks",
                        lambda cli, **kw: (seen.append(kw.get("reason")), real(cli, **kw)))
    return seen


# -- the kill case -------------------------------------------------------------


def test_a_turn_is_on_disk_before_the_session_ends(cli):
    _answering(cli)
    snapshots: list = []
    _drive(cli, "hello", lambda: snapshots.append(_files(cli)))
    (only,) = snapshots[0]
    assert only["session_id"] == cli.current_session_id
    assert [m["content"] for m in only["messages"]] == ["hello", "re: hello"]


def test_many_turns_keep_one_file_for_the_session(cli):
    _answering(cli)
    counts: list = []
    probe = lambda: counts.append(len(_files(cli)))  # noqa: E731
    _drive(cli, "one", probe, "two", probe, "three", probe)
    assert counts == [1, 1, 1]
    (final,) = _files(cli)
    assert len(final["messages"]) == 6


def test_the_checkpoint_does_not_evict_other_sessions(cli):
    """Rotation keeps 10 files. One file per *save* would push the other nine
    sessions out within a few turns."""
    for i in range(9):
        save_session([{"role": "user", "content": f"old {i}"}], "m",
                     session_id=f"old-{i}", project_root=cli.agent.working_directory)
    _answering(cli)
    _drive(cli, *["turn"] * 5)
    ids = {f["session_id"] for f in _files(cli)}
    assert {f"old-{i}" for i in range(9)} <= ids and len(ids) == 10


@pytest.mark.parametrize("fail", [KeyboardInterrupt(), RuntimeError("provider down")])
def test_an_interrupted_or_failed_turn_keeps_the_prompt(cli, fail):
    _answering(cli, fail=fail)
    snapshots: list = []
    _drive(cli, "please keep me", lambda: snapshots.append(_files(cli)))
    (only,) = snapshots[0]
    assert only["messages"][-1]["content"] == "please keep me"


def test_an_exception_escaping_the_loop_still_saves(cli):
    """``AgentaoCLI.run``'s ``finally`` is the last net."""
    def loop():
        cli.agent.messages.append({"role": "user", "content": "last words"})
        raise KeyboardInterrupt
    cli._run_loop = loop
    cli.print_welcome = lambda: None
    with pytest.raises(KeyboardInterrupt):
        cli.run()
    (only,) = _files(cli)
    assert only["messages"][-1]["content"] == "last words"


# -- not a session end -----------------------------------------------------------


def test_a_checkpoint_is_not_a_session_end(cli, monkeypatch):
    ends = _count_session_end(monkeypatch)
    _answering(cli)
    seen: list = []
    _drive(cli, "a", "b", lambda: seen.append(list(ends)))
    assert seen == [[]]
    assert ends == ["prompt_input_exit"]


def test_exit_leaves_one_file_and_run_adds_none(cli):
    """``/exit``'s save replaces the checkpoint, and the ``finally`` in
    ``run`` finds nothing new to write."""
    _answering(cli)
    cli.print_welcome = lambda: None
    cli._get_user_input = iter(["hi", "/exit"]).__next__
    cli.run()
    (only,) = _files(cli)
    assert len(only["messages"]) == 2


# -- session boundaries ----------------------------------------------------------


def test_clear_keeps_the_ended_session_and_starts_a_new_file(cli):
    _answering(cli)
    first: list = []
    _drive(cli, "before", lambda: first.append(cli.current_session_id), "/clear", "after")
    by_id = {f["session_id"]: f for f in _files(cli)}
    assert len(by_id) == 2 and len(_files(cli)) == 2
    assert [m["content"] for m in by_id[first[0]]["messages"]] == ["before", "re: before"]


def test_resume_never_replaces_the_session_it_leaves(cli):
    project = cli.agent.working_directory
    save_session([{"role": "user", "content": "from yesterday"}], "m",
                 session_id="older", project_root=project)
    _answering(cli)
    left: list = []
    after_resume: list = []

    def resume():
        from agentao.cli.commands import resume_session
        left.append(cli.current_session_id)
        resume_session(cli, "older")
        after_resume.append(len(_files(cli)))

    def ctrl_c_at_the_prompt():
        raise KeyboardInterrupt

    _drive(cli, "mine", resume, ctrl_c_at_the_prompt,
           lambda: after_resume.append(len(_files(cli))), "more")
    # Resuming wrote nothing, and neither did a Ctrl-C on the idle prompt
    # after it: the loaded history is already on disk.
    assert after_resume == [2, 2]
    ids = [f["session_id"] for f in _files(cli)]
    assert ids.count(left[0]) == 1           # the checkpoint of the session left
    mine = next(f for f in _files(cli) if f["session_id"] == left[0])
    assert [m["content"] for m in mine["messages"]] == ["mine", "re: mine"]
    (resumed,) = [f for f in _files(cli) if f["session_id"] == "older"]
    assert len(resumed["messages"]) == 3


def test_a_resumed_sessions_turn_replaces_the_file_it_was_loaded_from(cli):
    """Codex review of #371: a resumed session is not a new one. Its first save
    replaces the loaded file, so resuming and chatting N times leaves one file
    for it — not N+1, each pushing another session out of the rotation."""
    project = cli.agent.working_directory
    save_session([{"role": "user", "content": "from yesterday"}], "m",
                 session_id="older", project_root=project)
    _answering(cli)
    from agentao.cli.commands import resume_session
    resume = lambda: resume_session(cli, "older")  # noqa: E731
    _drive(cli, resume, "one", resume, "two", resume, "three")
    older = [f for f in _files(cli) if f["session_id"] == "older"]
    assert len(older) == 1
    # Each resume loads the newest file, so every turn is kept: 1 + 3 × 2.
    assert len(older[0]["messages"]) == 7


def test_a_launch_resume_is_tracked_through_the_session_start(cli):
    """``--resume`` records the file before ``run_loop`` dispatches the session
    start; that start must not drop the record."""
    project = cli.agent.working_directory
    save_session([{"role": "user", "content": "from yesterday"}], "m",
                 session_id="older", project_root=project)
    from agentao.cli.commands import resume_session
    resume_session(cli, "older", at_launch=True)
    _answering(cli)
    _drive(cli, "again")
    (only,) = _files(cli)
    assert only["session_id"] == "older" and len(only["messages"]) == 3


# -- save_session's own contract -------------------------------------------------


def test_supersedes_removes_only_an_earlier_save_of_the_same_session(tmp_path):
    first, sid = save_session([{"role": "user", "content": "1"}], "m", project_root=tmp_path)
    other, _ = save_session([{"role": "user", "content": "x"}], "m",
                            session_id="someone-else", project_root=tmp_path)
    second, _ = save_session([{"role": "user", "content": "2"}], "m", session_id=sid,
                             project_root=tmp_path, supersedes=first)
    assert not first.exists() and second.exists() and other.exists()
    # A file of another session is never removed, whatever the caller passes.
    third, _ = save_session([{"role": "user", "content": "3"}], "m", session_id=sid,
                            project_root=tmp_path, supersedes=other)
    assert other.exists() and third.exists()


def test_a_save_leaves_no_partial_file(tmp_path):
    path, _ = save_session([{"role": "user", "content": "x"}], "m", project_root=tmp_path)
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_a_failed_save_leaves_no_partial_file(tmp_path):
    """An unserializable message fails the dump; the ``.tmp`` must not stay."""
    with pytest.raises(TypeError):
        save_session([{"role": "user", "content": object()}], "m", project_root=tmp_path)
    assert list((tmp_path / ".agentao" / "sessions").iterdir()) == []


def test_supersedes_carries_created_at(tmp_path):
    first, sid = save_session([{"role": "user", "content": "1"}], "m", project_root=tmp_path)
    created = json.loads(first.read_text(encoding="utf-8"))["created_at"]
    second, _ = save_session([{"role": "user", "content": "2"}], "m", session_id=sid,
                             project_root=tmp_path, supersedes=first)
    assert json.loads(second.read_text(encoding="utf-8"))["created_at"] == created
