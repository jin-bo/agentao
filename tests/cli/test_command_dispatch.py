"""Slash-command dispatch table.

``run_loop`` used to dispatch through a 31-branch ``if/elif`` chain that no
test drove. The table replaces the chain and, more usefully, makes the
command vocabulary a value that can be asserted against the other two
places it is written down: tab-completion (``_utils._SLASH_COMMANDS``) and
``/help`` (``help_text``). The drift check below is the point of the
refactor — it caught ``/sandbox`` missing from completions.
"""

from __future__ import annotations

import re

import pytest

from agentao.cli import help_text
from agentao.cli._utils import _SLASH_COMMAND_HINTS, _SLASH_COMMANDS
from agentao.cli.input_loop import _EXIT_COMMANDS, _build_command_table

# ``/plugin`` is an accepted singular alias of ``/plugins``; it is
# deliberately not advertised in completions or /help.
_UNADVERTISED_ALIASES = {"plugin"}


@pytest.fixture(scope="module")
def table():
    return _build_command_table()


def _completion_roots() -> set[str]:
    return {entry[1:].split()[0] for entry in _SLASH_COMMANDS}


def test_table_covers_every_advertised_command(table):
    """Anything tab-completion offers must actually dispatch."""
    dispatchable = set(table) | set(_EXIT_COMMANDS)
    orphaned = _completion_roots() - dispatchable
    assert not orphaned, f"completions offer non-dispatchable commands: {sorted(orphaned)}"


def test_every_command_is_tab_completable(table):
    """The drift this test exists to catch: /sandbox was dispatchable but
    never suggested, so it was undiscoverable from the prompt."""
    dispatchable = (set(table) | set(_EXIT_COMMANDS)) - _UNADVERTISED_ALIASES
    missing = dispatchable - _completion_roots()
    assert not missing, f"dispatchable but not tab-completable: {sorted(missing)}"


def test_every_command_appears_in_help(table):
    src = open(help_text.__file__, encoding="utf-8").read()
    mentioned = set(re.findall(r"/([a-z]+)", src))
    dispatchable = (set(table) | set(_EXIT_COMMANDS)) - _UNADVERTISED_ALIASES
    missing = dispatchable - mentioned
    assert not missing, f"dispatchable but absent from /help: {sorted(missing)}"


def test_completion_hints_reference_real_commands():
    """A hint for a command that no longer exists is dead UI."""
    unknown = {h for h in _SLASH_COMMAND_HINTS if h not in _SLASH_COMMANDS}
    assert not unknown, f"hints for unlisted commands: {sorted(unknown)}"


def test_exit_is_not_in_the_table(table):
    """``/exit`` breaks the loop — control flow the table cannot express.
    If it ever lands in the table it would silently stop quitting."""
    assert not (_EXIT_COMMANDS & set(table))


def test_every_handler_accepts_cli_and_args(table):
    """Uniform ``(cli, args)`` shape — the invariant the loop relies on."""
    import inspect

    for name, handler in table.items():
        sig = inspect.signature(handler)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert len(positional) >= 2, f"/{name} handler does not take (cli, args)"


def test_table_is_rebuilt_not_cached_across_calls():
    """Handlers are looked up once per loop start; two builds must agree."""
    assert set(_build_command_table()) == set(_build_command_table())


# ── Behavior preserved through the extraction ───────────────────────


class _Skills:
    def __init__(self):
        self.available_skills = {"alpha": object()}
        self.activated = []
        self.reloaded = False

    def list_available_skills(self):
        return list(self.available_skills)

    def activate_skill(self, name, reason):
        self.activated.append((name, reason))
        return f"Activated {name}"

    def deactivate_skill(self, name):
        return True

    def disable_skill(self, name):
        return f"disabled {name}"

    def enable_skill(self, name):
        return f"enabled {name}"

    def reload_skills(self):
        self.reloaded = True


class _Agent:
    def __init__(self):
        self.skill_manager = _Skills()


class _Cli:
    def __init__(self):
        self.agent = _Agent()
        self.listed = False

    def list_skills(self):
        self.listed = True


@pytest.fixture
def printed(monkeypatch):
    from agentao.cli.commands import skills as skills_mod

    out: list[str] = []
    monkeypatch.setattr(
        skills_mod.console, "print", lambda *a, **k: out.append(" ".join(map(str, a)))
    )
    return out


def test_skills_bare_lists(printed):
    from agentao.cli.commands import handle_skills_command

    cli = _Cli()
    handle_skills_command(cli, "")
    assert cli.listed


def test_skills_activate_passes_manual_reason(printed):
    from agentao.cli.commands import handle_skills_command

    cli = _Cli()
    handle_skills_command(cli, "activate alpha")
    assert cli.agent.skill_manager.activated == [
        ("alpha", "Manually activated via /skills activate")
    ]


def test_skills_deactivate_unknown_lists_available(printed):
    from agentao.cli.commands import handle_skills_command

    handle_skills_command(_Cli(), "deactivate nope")
    assert "Unknown skill 'nope'" in printed[-1]
    assert "alpha" in printed[-1]


def test_skills_missing_arg_shows_usage(printed):
    from agentao.cli.commands import handle_skills_command

    handle_skills_command(_Cli(), "activate")
    assert "Usage: /skills activate" in printed[-1]


def test_skills_unknown_subcommand(printed):
    from agentao.cli.commands import handle_skills_command

    handle_skills_command(_Cli(), "bogus")
    assert "Unknown subcommand 'bogus'" in printed[-1]


def test_skills_reload_reports_count(printed):
    from agentao.cli.commands import handle_skills_command

    cli = _Cli()
    handle_skills_command(cli, "reload")
    assert cli.agent.skill_manager.reloaded
    assert "1 available" in printed[-1]
