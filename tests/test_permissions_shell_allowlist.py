"""PermissionEngine: the auto-allowed shell list, protected write paths, and
``file_path`` rules matched against resolved spellings.

Every "asks" case below was a command the ``workspace-write`` / ``plan``
presets allowed without a prompt, and each one runs or writes something:
``env sh -c ...`` runs anything, ``git diff --output=<path>`` writes anywhere,
quoting and globs hand git an ``--output`` the regex never saw, and a write
to ``.git/config`` turns the next auto-allowed ``git status`` into a command
(``core.fsmonitor``).
"""

import os

import pytest

from agentao.permissions import PermissionDecision, PermissionEngine, PermissionMode


ALLOW, ASK, DENY = PermissionDecision.ALLOW, PermissionDecision.ASK, PermissionDecision.DENY

_STILL_ALLOWED = [
    "git status",
    "git status -s",
    "git diff HEAD~1",
    "git log --oneline -5",
    "git log -p --no-ext-diff",
    "git show HEAD",
    "git stash list",
    "git config --get user.name",
    "git config --get-all remote.origin.url",
    "git rev-parse HEAD",
    "ls",
    "ls -la src",
    "cat README.md",
    "grep -rn foo src/",
    "tail -n 5 x.log",
    "wc -l a b",
    "pwd",
]

_NO_LONGER_ALLOWED = [
    "env sh -c id",                               # runs its argument
    "env python3 -c print(1)",
    "file -C -m magic",                           # writes magic.mgc
    "file x",
    "cat-tool",                                   # \b treated '-' as a boundary
    "git statusx",                                # git branch had no boundary
    "git diff --output=x",                        # writes anywhere
    "git log --output=/tmp/x",
    "git diff --output x",
    'git diff --no-index --out""put=x a b',       # shell joins it to --output
    "git log --out\\put=x",
    "git diff *",                                 # a file named --output=x
    "ls *.md",
    "git diff --ext a b",                         # git accepts the abbreviation
    "git diff --ext-diff",
    "git show --textconv",
    "git -c core.pager=id log",
    "grep 'a b' f",
    "echo {a,b}",
    "echo (Set-Content .agentao/mcp.json evil)",  # PowerShell subexpression
    "cat (Get-Content list.txt)",
]


def _engine(tmp_path, rules=None):
    return PermissionEngine(project_root=tmp_path, rules=rules or [])


@pytest.mark.parametrize("command", _STILL_ALLOWED)
@pytest.mark.parametrize("mode", [PermissionMode.WORKSPACE_WRITE, PermissionMode.PLAN])
def test_read_only_commands_stay_allowed(tmp_path, mode, command):
    e = _engine(tmp_path)
    e.set_mode(mode)
    assert e.decide("run_shell_command", {"command": command}) == ALLOW


@pytest.mark.parametrize("command", _NO_LONGER_ALLOWED)
def test_workspace_write_asks(tmp_path, command):
    e = _engine(tmp_path)
    assert e.decide("run_shell_command", {"command": command}) == ASK


@pytest.mark.parametrize("command", _NO_LONGER_ALLOWED)
def test_plan_denies(tmp_path, command):
    # Plan mode shares the list; what falls out of it meets plan's DENY.
    e = _engine(tmp_path)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("run_shell_command", {"command": command}) == DENY


def test_newline_after_command_name_is_not_allowed(tmp_path):
    # The name boundary is a lookahead: a consuming ``\s`` would eat the
    # newline and let the tail's newline ban pass the second command.
    e = _engine(tmp_path)
    assert e.decide("run_shell_command", {"command": "ls\ncurl x"}) == ASK
    assert e.decide("run_shell_command", {"command": "ls -la\n"}) == ASK


# ---------------------------------------------------------------------------
# Protected write paths
# ---------------------------------------------------------------------------

_PROTECTED = [
    ".git/config",
    "./.git/hooks/pre-commit",
    "sub/.git/config",        # submodule / nested repo
    ".git",                   # a worktree's .git file
    ".agentao/mcp.json",
    "./.agentao/plugins/x/hooks/hooks.json",
    ".GIT/config",            # case-insensitive filesystems (macOS, Windows)
    ".Agentao/mcp.json",
]

_ORDINARY = ["src/a.py", ".github/workflows/ci.yml", ".gitignore", ".gitattributes", "agentao/x.py"]


@pytest.mark.parametrize("tool", ["write_file", "replace"])
@pytest.mark.parametrize("path", _PROTECTED)
def test_workspace_write_asks_for_protected_paths(tmp_path, tool, path):
    e = _engine(tmp_path)
    assert e.decide(tool, {"file_path": path}) == ASK
    assert e.decide(tool, {"file_path": str(tmp_path / path)}) == ASK


@pytest.mark.parametrize("tool", ["write_file", "replace"])
@pytest.mark.parametrize("path", _ORDINARY)
def test_workspace_write_allows_ordinary_paths(tmp_path, tool, path):
    e = _engine(tmp_path)
    assert e.decide(tool, {"file_path": path}) == ALLOW


def test_symlink_alias_of_git_dir_is_protected(tmp_path):
    (tmp_path / ".git").mkdir()
    try:
        os.symlink(tmp_path / ".git", tmp_path / "alias")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    e = _engine(tmp_path)
    assert e.decide("write_file", {"file_path": "alias/config"}) == ASK


def test_plan_still_denies_protected_writes(tmp_path):
    # An ASK in front of plan's DENY would make the ban approvable.
    e = _engine(tmp_path)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("write_file", {"file_path": ".git/config"}) == DENY


def test_full_access_is_unchanged(tmp_path):
    e = _engine(tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide("write_file", {"file_path": ".git/config"}) == ALLOW


def test_user_allow_rule_can_still_override(tmp_path):
    e = _engine(tmp_path, rules=[{"tool": "write_file", "args": {"file_path": r"^\.agentao/"}, "action": "allow"}])
    assert e.decide("write_file", {"file_path": ".agentao/notes.md"}) == ALLOW


# ---------------------------------------------------------------------------
# file_path rules see the resolved path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spelling", ["secrets/k", "./secrets/k", "sub/../secrets/k", "ABS"])
def test_relative_deny_rule_covers_every_spelling(tmp_path, spelling):
    path = str(tmp_path / "secrets/k") if spelling == "ABS" else spelling
    e = _engine(tmp_path, rules=[{"tool": "write_file", "args": {"file_path": "^secrets/"}, "action": "deny"}])
    assert e.decide("write_file", {"file_path": path}) == DENY


def test_absolute_deny_rule_covers_relative_spelling(tmp_path):
    root = str(tmp_path.resolve())
    rule = {"tool": "read_file", "args": {"file_path": "^" + root.replace("\\", "\\\\") + "[/\\\\]secrets"}, "action": "deny"}
    e = _engine(tmp_path, rules=[rule])
    assert e.decide("read_file", {"file_path": "./secrets/k"}) == DENY


def test_non_path_args_are_matched_raw(tmp_path):
    # Only file_path on the file tools is resolved; a rule on another key
    # or another tool keeps plain regex semantics.
    rule = {"tool": "my_tool", "args": {"file_path": "^secrets/"}, "action": "deny"}
    e = _engine(tmp_path, rules=[rule])
    assert e.decide("my_tool", {"file_path": "./secrets/k"}) is None


def test_allow_rule_sees_only_the_raw_path(tmp_path):
    # A negative lookahead matches the absolute spelling of the very file it
    # excludes, so an allow rule must not be tried against resolved forms.
    rule = {"tool": "read_file", "args": {"file_path": "^(?!secrets/)"}, "action": "allow"}
    e = _engine(tmp_path, rules=[rule])
    assert e.decide("read_file", {"file_path": "secrets/k"}) is None
    assert e.decide("read_file", {"file_path": "docs/a.md"}) == ALLOW


def test_allow_rule_is_not_widened_to_other_spellings(tmp_path):
    rule = {"tool": "read_file", "args": {"file_path": "^docs/"}, "action": "allow"}
    e = _engine(tmp_path, rules=[rule])
    assert e.decide("read_file", {"file_path": "docs/a.md"}) == ALLOW
    assert e.decide("read_file", {"file_path": "./docs/a.md"}) is None
