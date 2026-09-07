"""The shell configuration surface: the rule label, the shell block, and the one record.

Two questions, and they are separate on purpose. A permission rule's ``dialect`` label says
which syntax that rule's pattern was written for; the ``shell`` block says which interpreter
this host will actually run. Conflating them would let a repository-supplied rule choose the
interpreter, which is exactly what the user-level-only restriction below exists to prevent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentao.capabilities.shell_spec import ShellDialect
from agentao.embedding.permission_loader import (
    PermissionConfig,
    PermissionConfigError,
    load_permission_config,
)
from agentao.permissions import (
    rule_matches_dialect,
    unspecified_shell_rules,
    validate_permission_rules,
)


def write_config(root: Path, document: dict) -> Path:
    (root / "permissions.json").write_text(json.dumps(document), encoding="utf-8")
    return root


# ------------------------------------------------------- the rule's dialect label


@pytest.mark.parametrize("dialect", ["posix", "cmd", "powershell", "*"])
def test_the_four_labels_are_accepted(dialect):
    assert validate_permission_rules([{"tool": "run_shell_command", "action": "allow",
                                       "dialect": dialect}]) == []


def test_an_unknown_label_is_refused_rather_than_ignored():
    """A misspelled label that fell through would silently apply the rule everywhere."""
    errors = validate_permission_rules(
        [{"tool": "run_shell_command", "action": "allow", "dialect": "bash"}]
    )
    assert errors and "unknown dialect" in errors[0][1]


def test_the_label_names_a_dialect_and_not_an_interpreter():
    """`posix` covers bash, dash and zsh alike.

    What a regular expression can read is decided by the syntax, not by which interpreter
    was selected, so labelling by interpreter would split one answer across many names.
    """
    rule = {"tool": "run_shell_command", "action": "allow", "dialect": "posix"}
    assert rule_matches_dialect(rule, "posix") is True
    assert rule_matches_dialect(rule, "powershell") is False


def test_an_unlabelled_rule_still_matches_everything():
    """Every rule written before the label existed keeps working exactly as it did."""
    assert rule_matches_dialect({"tool": "*", "action": "allow"}, "powershell") is True
    assert rule_matches_dialect({"tool": "*", "action": "allow", "dialect": "*"}, "cmd") is True


def test_an_unlabelled_command_rule_is_unspecified_rather_than_universal():
    """The other half, and the reason the permissiveness above is safe.

    A rule matching on `args.command` was written against some shell's syntax and does not
    record which. On PowerShell there is no safe reading: applying it applies a pattern to a
    language it was not written for, and skipping it drops a rule its author relies on — so
    it is reported rather than silently resolved either way.
    """
    rules = [
        {"tool": "run_shell_command", "action": "deny", "args": {"command": "rm -rf"}},
        {"tool": "run_shell_command", "action": "deny", "args": {"command": "dd"},
         "dialect": "posix"},
        {"tool": "web_fetch", "action": "allow"},
    ]
    offenders = unspecified_shell_rules(rules)
    assert [index for index, _ in offenders] == [0]


# ---------------------------------------------------------------- the shell block


def test_a_complete_shell_block_loads(tmp_path):
    root = write_config(tmp_path, {
        "rules": [],
        "shell": {"path": "C:/pwsh/pwsh.exe", "dialect": "powershell"},
    })
    config = load_permission_config(project_root=root, user_root=root)
    assert config.shell is not None
    assert config.shell.dialect is ShellDialect.POWERSHELL
    assert config.shell.path == "C:/pwsh/pwsh.exe"


def test_a_dialect_on_its_own_is_the_ordinary_way_to_ask_for_powershell(tmp_path):
    """The pair used to be mandatory, which made the common case unreachable.

    Nobody wants to write out an install path that agentao can find, and requiring one meant
    the only way to enable PowerShell was to also pin a version of it by hand.
    """
    root = write_config(tmp_path, {"rules": [], "shell": {"dialect": "powershell"}})
    config = load_permission_config(project_root=root, user_root=root)
    assert config.shell is not None
    assert config.shell.dialect is ShellDialect.POWERSHELL and config.shell.path is None


def test_a_path_without_a_dialect_is_refused_and_says_which_half_is_missing(tmp_path):
    """A renamed launcher says nothing about the syntax it reads.

    And the syntax is what decides how the command floor reads the command, so guessing here
    would silently pick which grammar an unrecognised interpreter is scanned with.
    """
    root = write_config(tmp_path, {"rules": [], "shell": {"path": "C:/pwsh/pwsh.exe"}})
    with pytest.raises(PermissionConfigError) as exc:
        load_permission_config(project_root=root, user_root=root)
    assert "dialect" in str(exc.value)


@pytest.mark.parametrize("key", ["rung", "ladder", "allowlist", "env_passthrough",
                                 "allow_git_bash"])
def test_the_key_set_is_closed(tmp_path, key):
    """A block that reads back as honoured while configuring nothing is the failure here.

    Every one of these was a real key at some point; accepting them now would leave a
    configuration that loads cleanly and changes nothing at all.
    """
    root = write_config(tmp_path, {"rules": [], "shell": {key: True}})
    with pytest.raises(PermissionConfigError, match=key):
        load_permission_config(project_root=root, user_root=root)


def test_an_unknown_dialect_names_the_ones_that_exist(tmp_path):
    root = write_config(tmp_path, {"rules": [], "shell": {"dialect": "fish"}})
    with pytest.raises(PermissionConfigError, match="posix, cmd, powershell"):
        load_permission_config(project_root=root, user_root=root)


# ------------------------------------------------- user scope, and one record


def test_a_workspace_shell_block_is_not_read(tmp_path):
    """Shell configuration is user-level or host, never the workspace.

    That is a trust boundary rather than a filing convention: a block checked into a
    repository would let the repository choose the interpreter the agent runs.
    """
    project = tmp_path / "project"
    (project / ".agentao").mkdir(parents=True)
    (project / ".agentao" / "permissions.json").write_text(
        json.dumps({"rules": [], "shell": {"path": "/evil/sh", "dialect": "posix"}}),
        encoding="utf-8",
    )
    config = load_permission_config(project_root=project, user_root=None)
    assert config.shell is None


def test_the_config_is_one_record_carrying_all_three(tmp_path):
    """Rules, sources and the shell block travel together through every root.

    The block previously had no route through any root at all, which is why this is a record
    and not a third return value: a tuple that grows a member is a shape every caller has to
    be edited to keep up with.
    """
    root = write_config(tmp_path, {
        "rules": [{"tool": "web_fetch", "action": "allow"}],
        "shell": {"path": "/usr/bin/bash", "dialect": "posix"},
    })
    config = load_permission_config(project_root=root, user_root=root)
    assert isinstance(config, PermissionConfig)
    assert len(config.rules) == 1
    assert config.sources and config.sources[0].startswith("user:")
    assert config.shell is not None


def test_every_composition_root_reads_the_same_record():
    """Three roots reading three shapes is how a key ends up honoured on one path.

    Checked by reading the source rather than by driving all three, because two of them build
    an ACP session; what matters is that none of them still calls the older rule-only loader.
    """
    import agentao.acp.session_load as session_load
    import agentao.acp.session_new as session_new
    import agentao.embedding.factory as factory

    for module in (factory, session_new, session_load):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "load_permission_config" in text, module.__name__
        assert "load_permission_rules(" not in text, module.__name__

# --------------------------------------------------- and it reaches the runtime


def test_the_block_changes_what_the_local_executor_reports():
    """A configuration key with a parser and no consumer is the failure this closes.

    Off Windows a PowerShell dialect answers ``Exhausted`` — there is no PowerShell to run,
    and falling back to a POSIX shell would read the body in a different language. What
    matters here is that the two answers *differ*, which is what a value with a consumer
    looks like.
    """
    from agentao.capabilities import LocalShellExecutor
    from agentao.capabilities.shell_spec import Exhausted, ShellBlock, ShellDialect, ShellSpec

    unset = LocalShellExecutor().shell_spec
    named = LocalShellExecutor(
        shell_block=ShellBlock(path="/bin/zsh", dialect=ShellDialect.POSIX)
    ).shell_spec

    assert isinstance(unset, ShellSpec) and unset.interpreter is None
    assert isinstance(named, ShellSpec) and named.interpreter == "/bin/zsh"

    if sys.platform != "win32":
        elsewhere = LocalShellExecutor(
            shell_block=ShellBlock(dialect=ShellDialect.POWERSHELL)
        ).shell_spec
        assert isinstance(elsewhere, Exhausted) and "Windows-only" in elsewhere.reason


def test_the_executor_reports_one_spec_object_per_call():
    """A call holds one spec until re-resolution swaps it.

    Minting a fresh one per read would also re-run PowerShell discovery per read, which is a
    filesystem walk on the permission path of every shell command.
    """
    from agentao.capabilities import LocalShellExecutor

    executor = LocalShellExecutor()
    assert executor.shell_spec is executor.shell_spec


def test_the_factory_gives_the_executor_the_block_it_loaded(tmp_path, monkeypatch):
    """The block travels with the rules, and it lands somewhere.

    Asserted through the factory rather than by calling the loader and the constructor in
    sequence, because composing them by hand is a restatement of the code rather than a test
    of it — the question is whether the composition root actually does it.
    """
    from unittest.mock import Mock, patch

    from agentao.capabilities import LocalShellExecutor
    from agentao.embedding import build_from_environment

    # The loader reads the *user* scope, which is ``~/.agentao``, so the file has to land
    # where ``user_root()`` will look rather than beside the project.
    user_dir = tmp_path / ".agentao"
    user_dir.mkdir()
    write_config(user_dir, {"rules": [], "shell": {"dialect": "posix"}})
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    with patch("agentao.agent.LLMClient") as mock_llm_cls:
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "gpt-test"
        mock_llm.api_key = "test-key"
        mock_llm.base_url = "https://api.example.com/v1"
        mock_llm.temperature = 0.2
        mock_llm_cls.return_value = mock_llm
        agent = build_from_environment(working_directory=tmp_path)

    assert isinstance(agent.shell, LocalShellExecutor)
    assert agent.shell._shell_block is not None
    assert agent.shell._shell_block.dialect is ShellDialect.POSIX


# ------------------------------------------------- the label and the block, together


def test_selecting_powershell_warns_about_rules_whose_syntax_is_unknown(tmp_path, caplog):
    """A rule matching on ``args.command`` was written for *some* shell and does not say which.

    Reported rather than resolved. Refusing the interpreter over it would make PowerShell
    unreachable for almost everyone — an unlabelled command rule is the ordinary kind — and
    applying it silently is what this exists to stop being silent.
    """
    root = write_config(tmp_path, {
        "rules": [
            {"tool": "run_shell_command", "action": "deny", "args": {"command": "rm -rf"}},
            {"tool": "run_shell_command", "action": "deny", "args": {"command": "dd"},
             "dialect": "posix"},
        ],
        "shell": {"dialect": "powershell"},
    })
    with caplog.at_level("WARNING"):
        load_permission_config(project_root=root, user_root=root)
    assert "rules[0]" in caplog.text
    assert "rules[1]" not in caplog.text


@pytest.mark.parametrize("block", [{"dialect": "posix"}, {"dialect": "cmd"}, None])
def test_the_other_dialects_do_not_warn(tmp_path, caplog, block):
    """Those rules keep working exactly as they always have, so there is nothing to say."""
    document = {"rules": [
        {"tool": "run_shell_command", "action": "deny", "args": {"command": "rm -rf"}}
    ]}
    if block is not None:
        document["shell"] = block
    root = write_config(tmp_path, document)
    with caplog.at_level("WARNING"):
        load_permission_config(project_root=root, user_root=root)
    assert "args.command" not in caplog.text
