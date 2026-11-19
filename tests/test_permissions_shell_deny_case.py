"""A shell ``deny`` / ``ask`` rule also matches the command in lower case.

On macOS's and Windows' default filesystems ``GIT push`` runs git, and
PowerShell and cmd ignore case, so ``GIT push`` slipped past a ``deny`` on
``git push`` and ``RM -rf`` past the preset's ``rm -rf`` deny. An ``allow``
still matches only the raw text, so this never grants more.
"""

from pathlib import Path

import pytest

from agentao.permissions import PermissionDecision, PermissionEngine

ALLOW, ASK, DENY = PermissionDecision.ALLOW, PermissionDecision.ASK, PermissionDecision.DENY


def _decide(engine, command):
    return engine.decide("run_shell_command", {"command": command})


@pytest.mark.parametrize("command", [
    "git push origin main",
    "GIT push origin main",
    "Git Push origin main",
    "GIT PUSH",
])
def test_user_deny_matches_any_case(tmp_path: Path, command):
    engine = PermissionEngine(
        rules=[
            {"tool": "run_shell_command", "args": {"command": "git push"}, "action": "deny"},
            {"tool": "run_shell_command", "action": "allow"},
        ],
        project_root=tmp_path,
    )
    assert _decide(engine, command) is DENY


def test_user_ask_matches_any_case(tmp_path: Path):
    engine = PermissionEngine(
        rules=[
            {"tool": "run_shell_command", "args": {"command": "^curl "}, "action": "ask"},
            {"tool": "run_shell_command", "action": "allow"},
        ],
        project_root=tmp_path,
    )
    assert _decide(engine, "CURL https://x.example") is ASK


@pytest.mark.parametrize("command", ["RM -rf build", "Rm -rf build", "rm -Rf build", "SUDO ls", "MKFS /dev/x"])
def test_workspace_write_preset_deny_matches_any_case(tmp_path: Path, command):
    engine = PermissionEngine(rules=[], project_root=tmp_path)
    assert _decide(engine, command) is DENY


def test_a_deny_pattern_written_in_upper_case_still_matches_raw(tmp_path: Path):
    engine = PermissionEngine(
        rules=[{"tool": "run_shell_command", "args": {"command": "-X DELETE"}, "action": "deny"}],
        project_root=tmp_path,
    )
    assert _decide(engine, "curl -X DELETE https://x.example") is DENY


@pytest.mark.parametrize("command", ["Remove-Item x", "remove-item x", "REMOVE-ITEM x"])
def test_a_mixed_case_pattern_matches_any_case(tmp_path: Path, command):
    engine = PermissionEngine(
        rules=[
            {"tool": "run_shell_command", "args": {"command": "Remove-Item"}, "action": "deny"},
            {"tool": "run_shell_command", "action": "allow"},
        ],
        project_root=tmp_path,
    )
    assert _decide(engine, command) is DENY


def test_inline_flag_keeps_part_of_a_pattern_exact(tmp_path: Path):
    engine = PermissionEngine(
        rules=[
            {"tool": "run_shell_command", "args": {"command": "curl (?-i:-x)"}, "action": "deny"},
            {"tool": "run_shell_command", "action": "allow"},
        ],
        project_root=tmp_path,
    )
    assert _decide(engine, "CURL -x proxy:1 https://a") is DENY
    assert _decide(engine, "curl -X POST https://a") is ALLOW


def test_a_bad_deny_pattern_falls_back_to_equality_without_case(tmp_path: Path):
    engine = PermissionEngine(
        rules=[
            {"tool": "run_shell_command", "args": {"command": "rm ("}, "action": "deny"},
            {"tool": "run_shell_command", "action": "allow"},
        ],
        project_root=tmp_path,
    )
    assert _decide(engine, "RM (") is DENY


def test_allow_is_not_widened(tmp_path: Path):
    engine = PermissionEngine(
        rules=[{"tool": "run_shell_command", "args": {"command": "^git status$"}, "action": "allow"}],
        project_root=tmp_path,
    )
    assert _decide(engine, "git status") is ALLOW
    assert _decide(engine, "GIT status") is not ALLOW


def test_other_tools_keep_case_sensitive_matching(tmp_path: Path):
    engine = PermissionEngine(
        rules=[{"tool": "web_search", "args": {"query": "secret"}, "action": "deny"}],
        project_root=tmp_path,
    )
    assert engine.decide("web_search", {"query": "secret"}) is DENY
    assert engine.decide("web_search", {"query": "SECRET"}) is not DENY
