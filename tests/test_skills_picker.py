"""The ``/skills`` checkbox picker (``agentao/cli/skills_picker.py``).

The model is driven against a real ``SkillManager`` on disk, so a toggle is
checked by what ``skills_config.json`` holds, not by a mock's call log. The
key tests run the real prompt_toolkit ``Application`` on a pipe input.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agentao.skills.manager as _mod
from agentao.cli.skills_picker import (
    MAX_VISIBLE_ROWS,
    SkillsPickerModel,
    build_skills_picker_app,
)
from agentao.skills.manager import SkillManager


def _write_skill(skills_dir: Path, name: str, description: str = "A skill") -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n## Body\n",
        encoding="utf-8",
    )


def _manager(tmp_path, names, disabled=()):
    g = tmp_path / "global"
    for n in names:
        _write_skill(g, n, f"Does {n} things")
    cfg = tmp_path / ".agentao" / "skills_config.json"
    if disabled:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"disabled_skills": list(disabled)}), encoding="utf-8")
    with patch.multiple(_mod, _GLOBAL_SKILLS_DIR=g, _BUNDLED_SKILLS_DIR=tmp_path / "b"):
        return SkillManager(working_directory=tmp_path)


def _on_disk(tmp_path):
    cfg = tmp_path / ".agentao" / "skills_config.json"
    if not cfg.exists():
        return set()
    return set(json.loads(cfg.read_text(encoding="utf-8")).get("disabled_skills", []))


def _row(model, name):
    return next(r for r in model.rows if r.name == name)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def test_rows_show_enabled_state_and_list_a_stale_disabled_name(tmp_path):
    # ``ghost`` is disabled but not found by the scan (#270): the picker
    # must list it, or the only way to clear it is to type its name.
    m = _manager(tmp_path, ["alpha", "beta"], disabled=["beta", "ghost"])
    model = SkillsPickerModel(m)
    assert [r.name for r in model.rows] == ["alpha", "beta", "ghost"]
    assert _row(model, "alpha").enabled
    assert not _row(model, "beta").enabled
    assert _row(model, "ghost").kind == "missing"


def test_toggle_saves_each_change_at_once(tmp_path):
    m = _manager(tmp_path, ["alpha", "beta"])
    model = SkillsPickerModel(m)
    model.toggle()  # cursor on alpha
    assert _on_disk(tmp_path) == {"alpha"}
    assert not _row(model, "alpha").enabled
    model.toggle()
    assert _on_disk(tmp_path) == set()
    assert model.summary() == ""


def test_enabling_a_stale_name_clears_it(tmp_path):
    m = _manager(tmp_path, ["alpha"], disabled=["ghost"])
    model = SkillsPickerModel(m)
    model.set_query("ghost")
    model.toggle()
    assert _on_disk(tmp_path) == set()
    assert model.summary() == "Enabled 1 skill."


def test_disabling_an_active_skill_deactivates_it(tmp_path):
    m = _manager(tmp_path, ["alpha"])
    assert not m.activate_skill("alpha", "t").startswith("Error")
    model = SkillsPickerModel(m)
    assert _row(model, "alpha").active
    assert "disabling also deactivates it" in "".join(t for _, t in model.fragments())
    model.toggle()
    assert "alpha" not in m.active_skills
    assert not _row(model, "alpha").active


def test_a_failed_save_leaves_the_row_and_shows_the_error(tmp_path):
    m = _manager(tmp_path, ["alpha"])
    model = SkillsPickerModel(m)
    with patch.object(
        SkillManager, "_update_disabled_on_disk",
        side_effect=_mod._SkillConfigWriteError("Error: disk full"),
    ):
        model.toggle()
    row = _row(model, "alpha")
    assert row.enabled
    assert row.note == "Error: disk full"
    assert _on_disk(tmp_path) == set()
    assert model.summary() == ""


def test_a_manager_that_raises_does_not_crash_the_picker():
    def boom(name):
        raise RuntimeError("nope")

    fake = SimpleNamespace(
        available_skills={"a": {"description": "x"}}, disabled_skills=None,
        active_skills=None, disable_skill=boom, enable_skill=boom,
    )
    model = SkillsPickerModel(fake)
    model.toggle()
    assert model.rows[0].enabled
    assert "RuntimeError" in model.rows[0].note


def test_a_disable_written_by_another_process_shows_after_any_toggle(tmp_path):
    m = _manager(tmp_path, ["alpha", "beta"])
    model = SkillsPickerModel(m)
    cfg = tmp_path / ".agentao" / "skills_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"disabled_skills": ["beta"]}), encoding="utf-8")
    model.toggle()  # alpha
    assert not _row(model, "beta").enabled
    assert _on_disk(tmp_path) == {"alpha", "beta"}
    # Only the toggle made here counts in the summary.
    assert model.summary() == "Disabled 1 skill."


def test_a_cleared_stale_name_is_not_sent_back_to_the_manager(tmp_path):
    m = _manager(tmp_path, ["alpha"], disabled=["ghost"])
    model = SkillsPickerModel(m)
    model.set_query("ghost")
    model.toggle()  # clears it
    model.toggle()  # the manager would refuse an unknown name
    row = _row(model, "ghost")
    assert row.enabled
    assert "cannot be disabled here" in row.note
    assert "Known skills" not in row.note
    assert _on_disk(tmp_path) == set()


def test_search_filters_by_name_then_description_and_keeps_the_cursor(tmp_path):
    m = _manager(tmp_path, ["alpha", "beta", "gamma"])
    model = SkillsPickerModel(m)
    model.move(1)  # beta
    model.set_query("a")  # alpha, beta, gamma all match by name
    assert model.current_row().name == "beta"
    model.set_query("gam")
    assert [model.rows[i].name for i in model.filtered] == ["gamma"]
    model.set_query("zzz")
    assert model.current_row() is None
    model.toggle()  # no row: nothing happens
    assert "no matches" in "".join(t for _, t in model.fragments())


def test_window_scrolls_to_keep_the_cursor_visible(tmp_path):
    names = [f"s{i:02d}" for i in range(MAX_VISIBLE_ROWS + 5)]
    model = SkillsPickerModel(_manager(tmp_path, names))
    model.move(-1)  # wraps to the last row
    text = "".join(t for _, t in model.fragments())
    assert f"› [x] {names[-1]}" in text
    assert names[0] not in text
    assert "… 5 more" in text


def test_names_and_descriptions_are_cleaned_for_the_terminal():
    fake = SimpleNamespace(
        available_skills={"evil\x1b[2J": {"description": "line1\nline2\x07"}},
        disabled_skills=set(), active_skills={},
    )
    text = "".join(t for _, t in SkillsPickerModel(fake).fragments())
    assert "\x1b" not in text and "\x07" not in text
    assert "line1 line2" in text


def test_an_mcp_skill_loaded_by_uri_is_listed():
    fake = SimpleNamespace(
        available_skills={}, disabled_skills=set(),
        active_skills={"mcp:docs:skill://x/SKILL.md": {}},
    )
    row = SkillsPickerModel(fake).rows[0]
    assert row.kind == "mcp" and row.active and row.enabled


# ---------------------------------------------------------------------------
# Keys, through the real Application
# ---------------------------------------------------------------------------

def _run_keys(model, keys):
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as inp:
        inp.send_text(keys)
        build_skills_picker_app(model, input=inp, output=DummyOutput()).run()


def test_keys_move_toggle_search_and_close(tmp_path):
    m = _manager(tmp_path, ["alpha", "beta", "gamma"])
    model = SkillsPickerModel(m)
    # Down, Space (beta); type "gam", Enter (gamma); Ctrl+C closes.
    _run_keys(model, "\x1b[B gam\r\x03")
    assert _on_disk(tmp_path) == {"beta", "gamma"}
    assert model.summary() == "Disabled 2 skills."


def test_backspace_edits_the_search(tmp_path):
    model = SkillsPickerModel(_manager(tmp_path, ["alpha", "beta"]))
    _run_keys(model, "bx\x7f\x03")
    assert model.query == "b"


# ---------------------------------------------------------------------------
# /skills wiring
# ---------------------------------------------------------------------------

@pytest.fixture
def printed(monkeypatch):
    from agentao.cli.commands import skills as cmd

    out: list[str] = []
    monkeypatch.setattr(cmd.console, "print", lambda *a, **k: out.append(" ".join(map(str, a))))
    return out


def _cli(manager):
    calls = []
    return SimpleNamespace(
        agent=SimpleNamespace(skill_manager=manager),
        list_skills=lambda: calls.append("list"),
        calls=calls,
    )


def test_bare_skills_lists_when_not_on_a_terminal(monkeypatch, printed):
    from agentao.cli.commands import skills as cmd

    monkeypatch.setattr(cmd, "_interactive_terminal", lambda: False)
    cli = _cli(SimpleNamespace())
    cmd.handle_skills_command(cli, "")
    cmd.handle_skills_command(cli, "list")
    assert cli.calls == ["list", "list"]


def test_manage_off_a_terminal_says_so_and_lists(monkeypatch, printed):
    from agentao.cli.commands import skills as cmd

    monkeypatch.setattr(cmd, "_interactive_terminal", lambda: False)
    cli = _cli(SimpleNamespace())
    cmd.handle_skills_command(cli, "manage")
    assert cli.calls == ["list"]
    assert "needs an interactive terminal" in "\n".join(printed)


def test_bare_skills_on_a_terminal_opens_the_picker_and_prints_the_summary(
    tmp_path, monkeypatch, printed
):
    from agentao.cli import skills_picker
    from agentao.cli.commands import skills as cmd

    monkeypatch.setattr(cmd, "_interactive_terminal", lambda: True)

    def fake_run(manager):
        model = SkillsPickerModel(manager)
        model.toggle()
        return model

    monkeypatch.setattr(skills_picker, "run_skills_picker", fake_run)
    m = _manager(tmp_path, ["alpha"])
    cli = _cli(m)
    cmd.handle_skills_command(cli, "")
    assert cli.calls == []
    assert any("Disabled 1 skill." in line for line in printed)


def test_picker_with_no_skills_says_so(monkeypatch, printed):
    from agentao.cli.commands import skills as cmd

    monkeypatch.setattr(cmd, "_interactive_terminal", lambda: True)
    empty = SimpleNamespace(available_skills={}, disabled_skills=set(), active_skills={})
    cmd.handle_skills_command(_cli(empty), "manage")
    assert any("No skills available." in line for line in printed)
