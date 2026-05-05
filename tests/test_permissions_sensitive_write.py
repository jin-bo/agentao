"""PermissionEngine: sensitive-write preset rule.

Per permission-hardening-plan §7 (PR 5), workspace-write surfaces an
explicit ASK rule for shell-RC / credential-file writes — not a DENY,
so installers and devops scripts can proceed with operator confirmation.
``full-access`` deliberately does NOT carry this rule (literal full
access), and ``plan`` mode already denies all shell commands anyway.

Split out from the original monolithic ``test_permissions.py``.
"""

from agentao.permissions import PermissionDecision, PermissionEngine, PermissionMode


_SENSITIVE_WRITE_POSITIVE_CASES = [
    # Redirection (>, >>, FD redirects, no-space form, $HOME / ${HOME})
    "echo X >> ~/.bashrc",
    "echo X > ~/.zshrc",
    "echo X >~/.bashrc",                          # bash allows no space
    "cmd 2>> ~/.bashrc",                          # stderr append
    "cmd 2> ~/.netrc",
    "echo X >> $HOME/.bashrc",
    "echo X >> ${HOME}/.netrc",
    "echo X > ~/.pgpass",
    "echo X >> ~/.profile",
    "echo X >> ~/.zprofile",
    "echo X >> ~/.bash_profile",
    "echo X >> ~/.npmrc",
    "echo X >> ~/.pypirc",
    # tee
    "cat config | tee ~/.bashrc",
    "tee -a ~/.netrc < creds.txt",
    "echo X | tee -a ~/.zshrc",
    # cp / mv (with and without flags)
    "cp template ~/.zshrc",
    "cp -f template ~/.zshrc",
    "mv .bashrc.new ~/.bashrc",
    "mv -f staging ~/.netrc",
    # sed -i (plain and with .bak suffix)
    "sed -i 's/X/Y/' ~/.bashrc",
    "sed -i.bak 's/X/Y/' ~/.zshrc",
    "sed -i -E 's/X/Y/g' ~/.bashrc",
]

_SENSITIVE_WRITE_NEGATIVE_CASES = [
    # Reads must NOT trip — rule targets writes only.
    "cat ~/.bashrc",
    "grep PATH ~/.zshrc",
    "ls -la ~/.bashrc",
    "head -n 5 ~/.netrc",
    "diff ~/.bashrc ~/.bashrc.new",
    # Writes to non-sensitive files must NOT trip.
    "echo X >> /tmp/notes.txt",
    "echo X > /tmp/x.bashrc",                     # bashrc, but in /tmp
    "cp foo /tmp/.bashrc",                        # not under ~ / $HOME
    "tee /tmp/.netrc < creds",
    # Boundary precision: ``.bashrc.bak`` and ``.bashrc-old`` are NOT
    # ``.bashrc`` — the strict terminator (``\\b`` won't do; we use a
    # punct/whitespace lookahead) prevents this false positive.
    "echo X >> ~/.bashrc.bak",
    "echo X >> ~/.bashrc-old",
    "cp ~/.bashrc ~/.bashrc.bak",                 # source is sensitive,
                                                  # destination is .bak — only
                                                  # destination triggers, .bak
                                                  # does not.
    # ``source ~/.bashrc`` is a read (loads the file into the current shell).
    "source ~/.bashrc",
    ". ~/.bashrc",
]


def test_sensitive_write_preset_asks_in_workspace_write(tmp_path):
    """Every command in the positive matrix returns ASK in workspace-write.

    Today this is shadow-equivalent to the generic fallback ASK rule, but the
    explicit rule is what shows up in ``active_permissions()`` so a host UI
    can render "shell-RC writes will prompt" without re-deriving it. It also
    survives a future tightening of workspace-write that adds an ``allow``
    rule for general ``echo``/``cp`` commands — the sensitive-write rule
    short-circuits before the new allow rule fires.
    """
    e = PermissionEngine(project_root=tmp_path)  # workspace-write default
    for cmd in _SENSITIVE_WRITE_POSITIVE_CASES:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ASK, f"{cmd!r} should ASK in workspace-write"


def test_sensitive_write_preset_skips_reads_and_unrelated_writes(tmp_path):
    """Negative matrix: reads, non-sensitive writes, and ``.bak``-suffix
    targets must NOT match the sensitive-write rule. They still hit the
    fallback ``ask`` rule for ``run_shell_command`` (so the *outcome* in
    workspace-write is ASK either way), but the matched rule must be the
    fallback, not the sensitive-write rule — that's what we assert by
    comparing the matched rule's regex.
    """
    e = PermissionEngine(project_root=tmp_path)
    for cmd in _SENSITIVE_WRITE_NEGATIVE_CASES:
        detail = e.decide_detail("run_shell_command", {"command": cmd})
        matched = detail.matched_rule or {}
        # Either no rule matched (read-only allowlist) or the fallback
        # generic ``ask`` rule matched — but never the sensitive-write
        # regex.
        regex = (matched.get("args") or {}).get("command", "")
        assert "bashrc" not in regex, (
            f"{cmd!r} unexpectedly matched the sensitive-write rule: {regex!r}"
        )


def test_sensitive_write_preset_absent_from_full_access(tmp_path):
    """``full-access`` is literal full access (per plan §5.1, §7).

    A user who explicitly sets ``enable_hardline=False`` on full-access has
    declared "no surprises, no hidden floors" — the sensitive-write rule
    must not be silently introduced into that mode either.
    """
    e = PermissionEngine(project_root=tmp_path, enable_hardline=False)
    e.set_mode(PermissionMode.FULL_ACCESS)
    d = e.decide("run_shell_command", {"command": "echo X >> ~/.bashrc"})
    assert d == PermissionDecision.ALLOW


def test_sensitive_write_preset_visible_in_active_permissions(tmp_path):
    """The whole point of materializing this rule is host inspectability.

    ``active_permissions()`` must surface it so a host UI can answer the
    question "what does my current mode do on shell-RC writes?" without
    interpreting the engine's regex internals.
    """
    e = PermissionEngine(project_root=tmp_path)
    snapshot = e.active_permissions()
    rules = snapshot.rules if hasattr(snapshot, "rules") else snapshot["rules"]
    found = False
    for rule in rules:
        cmd_pattern = (rule.get("args") or {}).get("command", "")
        if "bashrc" in cmd_pattern and "zshrc" in cmd_pattern:
            assert rule.get("action") == "ask"
            found = True
            break
    assert found, "sensitive-write preset rule must appear in active_permissions()"
