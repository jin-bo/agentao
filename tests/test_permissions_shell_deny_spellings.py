"""Other spellings of a command no longer get past a shell ``deny``.

``g''it push``, ``"git" push``, ``g\\it push``, cmd's ``g^it push``,
``git.exe push``, ``/usr/bin/git push`` and ``git  push`` all run
``git push``. The preset's ``rm`` deny caught only ``rm -rf``. On cmd the
preset's read-only list allowed ``echo %VAR%``, which prints the variable.
"""

import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao.permissions import (
    _PRESET_SHELL_DENY_RE,
    PermissionDecision,
    PermissionEngine,
    _shell_command_canonical,
    _shell_command_canonicals,
)

ALLOW, ASK, DENY = PermissionDecision.ALLOW, PermissionDecision.ASK, PermissionDecision.DENY


def _spec(dialect):
    return SimpleNamespace(dialect=SimpleNamespace(value=dialect))


def _decide(engine, command, dialect="posix"):
    detail = engine.decide_detail(
        "run_shell_command", {"command": command}, shell_spec=_spec(dialect),
    )
    return detail.decision if detail is not None else None


@pytest.fixture
def deny_push(tmp_path: Path):
    return PermissionEngine(
        rules=[
            {"tool": "run_shell_command", "args": {"command": "^git push"}, "action": "deny"},
            {"tool": "run_shell_command", "action": "allow"},
        ],
        project_root=tmp_path,
    )


@pytest.mark.parametrize("command", [
    "git push",
    "g''it push",
    '"git" push',
    "g\\it push",
    "g^it push",
    "git.exe push",
    "GIT.EXE push",
    "/usr/bin/git push",
    "C:\\Tools\\git.exe push",
    "git  push",
    "git\tpush",
    " git push",
    "\tgit push",
])
def test_deny_catches_other_spellings(deny_push, command):
    assert _decide(deny_push, command) is DENY


def test_unrelated_command_still_allowed(deny_push):
    assert _decide(deny_push, "git status") is ALLOW


def test_allow_does_not_match_other_spellings(tmp_path: Path):
    engine = PermissionEngine(
        rules=[{"tool": "run_shell_command", "args": {"command": "^git status$"}, "action": "allow"}],
        project_root=tmp_path,
    )
    assert _decide(engine, "git status") is ALLOW
    assert _decide(engine, "/opt/evil/git status") is not ALLOW
    assert _decide(engine, "git.exe status") is not ALLOW


@pytest.mark.parametrize("command", [
    "rm -rf build",
    "rm -fr build",
    "rm -r -f build",
    "rm -f -r build",
    "rm --recursive --force build",
    "rm -rfv build",
    "xargs rm -rf",
    "find . -exec rm -rf {} +",
    "RM -Rf build",
])
def test_preset_denies_every_recursive_force_rm(tmp_path: Path, command):
    engine = PermissionEngine(rules=[], project_root=tmp_path)
    assert _decide(engine, command) is DENY


@pytest.mark.parametrize("command", [
    "rm -r build",
    "rm -f build",
    "rm -f x; rm -r y",
    "farm -rf",
    "docker run --rm img pytest -rf",
])
def test_preset_rm_deny_needs_both_flags_in_one_command(tmp_path: Path, command):
    engine = PermissionEngine(rules=[], project_root=tmp_path)
    assert _decide(engine, command) is not DENY


@pytest.mark.parametrize("text", [
    "rm" + " -r" * 20000,
    "rm " * 20000,
    "rm -r " * 5000,
    "rm" + " x" * 40000 + " -f",
    "rm -" + "r" * 20000 + "1",
    "rm -" + "f" * 20000 + "1 -r",
])
def test_preset_deny_is_linear(text):
    started = time.perf_counter()
    re.search(_PRESET_SHELL_DENY_RE, text, re.IGNORECASE)
    assert time.perf_counter() - started < 1.0


def test_suffix_is_stripped_only_from_the_program(tmp_path: Path):
    engine = PermissionEngine(
        rules=[
            {"tool": "run_shell_command", "args": {"command": r"^curl (?!https://docs\.example\.com)"}, "action": "deny"},
            {"tool": "run_shell_command", "action": "allow"},
        ],
        project_root=tmp_path,
    )
    assert _decide(engine, "curl https://docs.example.com") is ALLOW
    assert _decide(engine, "curl.exe https://evil.example") is DENY


@pytest.mark.parametrize("text", [
    ";a" * 30000,
    ("x" * 100 + ";") * 500,
    "a" * 50000 + ".x",
    "g''it " * 20000,
])
def test_canonical_spellings_are_linear(text):
    started = time.perf_counter()
    _shell_command_canonicals(text)
    assert time.perf_counter() - started < 1.0


def test_canonical_spelling():
    assert _shell_command_canonical("g''it  push") == "git push"
    assert _shell_command_canonical('/usr/bin/"git".exe push') == "git push"
    assert _shell_command_canonical("plain") == "plain"
    assert _shell_command_canonical("  git push") == "git push"
    assert _shell_command_canonical("ls && git.exe push") == "ls && git push"
    assert _shell_command_canonical("ping example.com") == "ping example.com"


@pytest.mark.parametrize("dialect,expected", [
    ("posix", ALLOW),
    ("cmd", ASK),
])
def test_preset_read_only_list_does_not_allow_cmd_expansion(tmp_path: Path, dialect, expected):
    engine = PermissionEngine(rules=[], project_root=tmp_path)
    assert _decide(engine, "echo %OPENAI_API_KEY%", dialect) is expected
    assert _decide(engine, "git status", dialect) is ALLOW


def test_full_access_still_allows_everything(tmp_path: Path):
    from agentao.permissions import PermissionMode

    engine = PermissionEngine(rules=[], project_root=tmp_path)
    engine.set_mode(PermissionMode.FULL_ACCESS)
    assert _decide(engine, "echo %X% && git status", "cmd") is ALLOW
