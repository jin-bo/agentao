"""`docs/design/powershell-support-contracts.py` — the executable half of the PowerShell spec.

The contract module *is* §3–§5 of `powershell-support-spec.zh.md`. Half of it is
signature-only (25 ``raise Unspecified`` seams and 22 ``...`` Protocol methods),
but the other 46 functions are real: the
ladder's selection order, the fail-closed cross-invariants, the effect-flag
derivation, the trusted-root walk, the workdir encoders, the length guard's
pre-checks. Until this file existed the only thing run against any of it was
``mypy --strict``, which checks types and not behaviour — and four defects found
by review were an earlier fix being silently undone in a function nothing tested
(rung selection twice, the pinned-environment validator, the decision entry).

So the rule here is narrow: **only functions with real bodies, and only the
behaviour a rule in the spec names.** A seam is stubbed, never asserted on. Each
test cites the rule ID it pins, so a rule that changes has a place to land.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "design" / "powershell-support-contracts.py"


def _load():
    spec = importlib.util.spec_from_file_location("powershell_support_contracts", CONTRACT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["powershell_support_contracts"] = mod
    spec.loader.exec_module(mod)
    return mod


if not CONTRACT.exists():  # pragma: no cover - the design set may be absent in a trimmed checkout
    pytest.skip("contract module missing", allow_module_level=True)

c = _load()


# ------------------------------------------------------------------ fixtures


def image(path: str = r"C:\Prog\pwsh.exe", sha: str = "aa", subject: str = "subj"):
    return c.ResolvedImage(
        canonical_path=c.AbsPath(path),
        filesystem_identity=c.FsId("fs-1"),
        execution_subject=c.Subject(subject),
        content_identity=c.HashPin(path=c.AbsPath(path), sha256=c.Sha256(sha)),
    )


def launcher(path: str = r"C:\Prog\pwsh.exe", sha: str = "aa"):
    return c.LauncherIdentity(image=image(path, sha), launcher_hash=c.Sha256(sha))


def pinned(**over):
    """A Windows `PinnedEnv` with every mandatory field answered (ENV-06f)."""
    fields = dict(
        system_root=c.AbsDir(r"C:\Windows"), windir=c.AbsDir(r"C:\Windows"), system_drive=c.DriveSpec("C:"),
        program_data=c.AbsDir(r"C:\ProgramData"), program_files=c.AbsDir(r"C:\Program Files"),
        program_files_x86=None, program_w6432=None, common_program_files=c.AbsDir(r"C:\Program Files\Common Files"),
        common_program_files_x86=None, all_users_profile=c.AbsDir(r"C:\ProgramData"),
        public=c.AbsDir(r"C:\Users\Public"), com_spec=c.AbsFile(r"C:\Windows\System32\cmd.exe"),
        home=c.AbsDir(r"C:\Users\u"), user_profile=c.AbsDir(r"C:\Users\u"), home_drive=c.DriveSpec("C:"),
        home_path=c.RootRelPath(r"\Users\u"), appdata=c.AbsDir(r"C:\Users\u\AppData\Roaming"),
        local_appdata=c.AbsDir(r"C:\Users\u\AppData\Local"), temp=c.AbsDir(r"C:\Temp"), tmp=c.AbsDir(r"C:\Temp"),
        tmpdir=None,
    )
    fields.update(over)
    return c.PinnedEnv(**fields)


def interpreter(path: str = r"C:\Prog\pwsh.exe", sha: str = "aa"):
    return c.InterpreterIdentity(image=image(path, sha), launcher_hash=c.Sha256(sha), edition="Core",
                                 version="7.4.0", pshome=c.AbsPath(r"C:\Prog"), session_config=None)


def spec(**over):
    """A minimal *legal* spec: CMD × cmd, policy on. PowerShell rungs need an `InterpreterIdentity` (IMG-07)."""
    fields = dict(
        dialect=c.ShellDialect.CMD, rung=c.Rung.cmd, filesystem_is_local=True,
        execution_subject=c.Subject("subj"), identity_oracle=None, closed_env_established=False,
        launcher=launcher(), pinned_env=pinned(), env_passthrough=(), allowlist=(), explicit_shell=None,
        target_platform=c.Platform.WINDOWS, policy_enabled=True, fingerprint=c.Sha256("fp"),
    )
    fields.update(over)
    return c.ShellSpec(**fields)


ORACLE_DEFAULTS = {
    "canonicalize": lambda self, path: c.AbsPath(path),
    "subject_can_replace": lambda self, path, subject: False,
    "subject_can_replace_entries": lambda self, path, subject: False,
    "resolve_reparse": lambda self, path: c.ReparseResult(c.ReparseState.not_reparse, None),
    "resolves_on_target": lambda self, path: True,
    "publisher_trusted": lambda self, path: False,
    "image_signer": lambda self, path: None,
    "content_hash": lambda self, path: c.Sha256("aa"),
    "target_base_env": lambda self, subject: {},
    "target_path_entries": lambda self, subject: (),
    "target_project_root": lambda self: c.AbsPath(r"C:\repo"),
    "target_platform": lambda self: c.Platform.WINDOWS,
    "target_filesystem_is_local": lambda self: True,
    "target_pinned_env": lambda self, subject: pinned(),
    "resolve_image": lambda self, path, subject: image(path),
    "discover": lambda self, rung, subject: None,
    "read_identity": lambda self, img, dialect: c.LauncherIdentity(image=img, launcher_hash=c.Sha256("aa")),
    "resolve_pshome": lambda self, img: c.AbsPath(r"C:\Prog"),
    "read_config_sources": lambda self, pshome, subject: c.SessionConfig(session=None),
    "preflight": lambda self, identity, prelude: True,
}


def oracle(omit: tuple[str, ...] = (), **answers):
    """A stub `IdentityOracle`. `omit` drops methods outright — that is what SPEC-05c's 缺任一方法 means."""
    assert set(ORACLE_DEFAULTS) == set(c.ORACLE_METHODS), "ORACLE_METHODS drifted from this stub"
    ns = {k: v for k, v in ORACLE_DEFAULTS.items() if k not in omit}
    for name, value in answers.items():
        ns[name] = value if callable(value) else (lambda v: (lambda self, *a, **k: v))(value)
    return type("StubOracle", (), ns)()


class Pattern(c.ArgPattern):
    """`ArgPattern.matches` is a seam; the effect-flag tests care about which branch fires, not the shape language."""

    def matches(self, args):
        return any(isinstance(a, c.Literal) and a.text == self.pattern for a in args)


def lit(*words):
    return tuple(c.Literal(w) for w in words)


# ------------------------------------------------------------------ EFF: effect flags


def test_caller_scope_rides_the_execution_trigger_and_the_rebind_trigger():
    """EFF-07's `+` marks the *executes_input* table, so `iex` must emit rebinds_caller with no rebind trigger."""
    iex = c.TrustedEntry(
        name="iex", dialect=c.ShellDialect.POWERSHELL, kind=c.EntryKind.cmdlet, alias_target=None, reenters=True,
        rung_scope=frozenset({c.Rung.pwsh}), execution_triggers=(Pattern("go"),), rebind_triggers=(),
        caller_scope=True, predicate_positions=frozenset(), source="test",
    )
    assert iex.flags(lit("go")) == frozenset({c.EffectFlag.executes_input, c.EffectFlag.rebinds_caller})
    # ...and rebinds_caller alone must not taint: G04-18 keeps `iex 'Get-Date'; git status` passing.
    assert c.EffectFlag.rebinds_after not in iex.flags(lit("go"))
    assert iex.flags(lit("quiet")) == frozenset()


def test_a_rebind_trigger_still_carries_caller_scope():
    entry = c.TrustedEntry(
        name="set-alias", dialect=c.ShellDialect.POWERSHELL, kind=c.EntryKind.cmdlet, alias_target=None,
        reenters=False, rung_scope=frozenset({c.Rung.pwsh}), execution_triggers=(),
        rebind_triggers=(Pattern("bind"),), caller_scope=True, predicate_positions=frozenset(), source="test",
    )
    assert entry.flags(lit("bind")) == frozenset({c.EffectFlag.rebinds_after, c.EffectFlag.rebinds_caller})


def test_a_dynamic_token_in_a_predicate_position_is_opaque(monkeypatch):
    """EFF-06 / TOK-02: `Remove-Item $flags C:\\` must not reach the effect table as an inert command (G05-02)."""
    entry = c.TrustedEntry(
        name="Remove-Item", dialect=c.ShellDialect.POWERSHELL, kind=c.EntryKind.cmdlet, alias_target=None,
        reenters=False, rung_scope=frozenset({c.Rung.pwsh}), execution_triggers=(), rebind_triggers=(),
        caller_scope=False, predicate_positions=frozenset({0}), source="test",
    )
    cmd = c.Command(word=c.Literal("Remove-Item"), args=(c.Dynamic("variable"), c.Literal(r"C:\\")))
    monkeypatch.setattr(c, "analyse", lambda dialect, body: (cmd,))
    monkeypatch.setattr(c, "lookup", lambda word, s: entry)
    monkeypatch.setattr(c, "dangerous", lambda e, args: pytest.fail("EFF-06 must fire before the danger table"))
    out = c.analyse_body(spec(identity_oracle=oracle()), "Remove-Item $flags C:\\", ())
    assert isinstance(out.verdict, c.Deny) and out.verdict.reason.endswith(":EFF-06")


# ------------------------------------------------------------------ IMG: trusted roots and identity


def _chain(path, target=None):
    """A stub for ``ancestors_to_volume_root``, whose real signature also takes the target.

    The platform is a parameter and not a guess: IMG-01 walks the *target's* filesystem, and
    re-asking the oracle for it would break the one-call snapshot G18-14 pins.
    """
    parts = path.split("\\")
    return tuple("\\".join(parts[:i]) or parts[0] for i in range(len(parts) - 1, 0, -1))


def test_a_junction_to_its_own_parent_is_not_a_cycle(monkeypatch):
    """IMG-06c: the visited set tracks the reparse traversal, not ancestors already permission-checked."""
    monkeypatch.setattr(c, "ancestors_to_volume_root", _chain)
    links = {r"C:\Trusted\alias": r"C:\Trusted"}
    o = oracle(resolve_reparse=lambda self, p: (
        c.ReparseResult(c.ReparseState.resolved, links[p]) if p in links
        else c.ReparseResult(c.ReparseState.not_reparse, None)))
    assert c.trusted_root_chain(c.AbsPath(r"C:\Trusted\alias"), c.Subject("subj"), o, c.Platform.WINDOWS, c.ChainHead.directory) is True


def test_mutually_pointing_junctions_are_refused(monkeypatch):
    monkeypatch.setattr(c, "ancestors_to_volume_root", _chain)
    links = {r"C:\Loop\a": r"C:\Loop\b", r"C:\Loop\b": r"C:\Loop\a"}
    o = oracle(resolve_reparse=lambda self, p: (
        c.ReparseResult(c.ReparseState.resolved, links[p]) if p in links
        else c.ReparseResult(c.ReparseState.not_reparse, None)))
    assert c.trusted_root_chain(c.AbsPath(r"C:\Loop\a"), c.Subject("subj"), o, c.Platform.WINDOWS, c.ChainHead.directory) is False


def test_an_unresolvable_reparse_point_is_refused(monkeypatch):
    """IMG-06c: `error` is the third state — treating it as 'not a reparse point' passes an unchecked chain."""
    monkeypatch.setattr(c, "ancestors_to_volume_root", _chain)
    o = oracle(resolve_reparse=lambda self, p: c.ReparseResult(c.ReparseState.error, None))
    assert c.trusted_root_chain(c.AbsPath(r"C:\Prog\pwsh.exe"), c.Subject("subj"), o, c.Platform.WINDOWS, c.ChainHead.image) is False


def test_a_writable_ancestor_refuses_the_whole_chain(monkeypatch):
    monkeypatch.setattr(c, "ancestors_to_volume_root", _chain)
    o = oracle(subject_can_replace=lambda self, p, subject: p == r"C:\Prog")
    assert c.trusted_root_chain(c.AbsPath(r"C:\Prog\pwsh.exe"), c.Subject("subj"), o, c.Platform.WINDOWS, c.ChainHead.image) is False


def test_a_volume_root_that_only_accepts_new_entries_does_not_refuse_the_chain(monkeypatch):
    """IMG-06a's split. A stock `C:\` grants every standard user FILE_ADD_SUBDIRECTORY and
    none of DELETE / FILE_DELETE_CHILD / WRITE_DAC / WRITE_OWNER (evidence §3.23), so asking
    the *target* mask to the volume root made IMG-01 false everywhere, for everyone."""
    monkeypatch.setattr(c, "ancestors_to_volume_root", _chain)
    o = oracle(subject_can_replace=lambda self, p, subject: p == "C:",
               subject_can_replace_entries=lambda self, p, subject: False)
    assert c.trusted_root_chain(c.AbsPath(r"C:\Prog\pwsh.exe"), c.Subject("subj"), o, c.Platform.WINDOWS, c.ChainHead.image) is True


def test_an_ancestor_whose_entries_the_subject_controls_still_refuses(monkeypatch):
    monkeypatch.setattr(c, "ancestors_to_volume_root", _chain)
    o = oracle(subject_can_replace=lambda self, p, subject: False,
               subject_can_replace_entries=lambda self, p, subject: p == "C:")
    assert c.trusted_root_chain(c.AbsPath(r"C:\Prog\pwsh.exe"), c.Subject("subj"), o, c.Platform.WINDOWS, c.ChainHead.image) is False


@pytest.mark.parametrize(
    "signer, host_trust, allowlist_kind, expected",
    [
        ("CN=Contoso", False, "publisher", True),
        ("CN=Evil", False, "publisher", False),
        (None, False, "publisher", False),
        (None, False, "pin", True),
        (None, False, "wrong-pin", False),
        ("CN=Contoso", True, "empty", True),
        ("CN=Contoso", False, "empty", False),
    ],
)
def test_host_identity_has_three_routes(signer, host_trust, allowlist_kind, expected):
    """IMG-03b: a PublisherTrust entry must change the decision — otherwise the config value does not exist."""
    img = image()
    allowlists = {
        "publisher": (c.PublisherTrust(signer="CN=Contoso"),),
        "pin": (c.HashPin(path=img.canonical_path, sha256=c.Sha256("aa")),),
        "wrong-pin": (c.HashPin(path=img.canonical_path, sha256=c.Sha256("bb")),),
        "empty": (),
    }
    o = oracle(image_signer=lambda self, p: signer, publisher_trusted=lambda self, p: host_trust)
    assert c.host_identity_ok(img, allowlists[allowlist_kind], o) is expected


# ------------------------------------------------------------------ ENV: pinned environment


def test_a_missing_mandatory_pinned_value_is_a_rejection(monkeypatch):
    """ENV-06f: `child_env` omits a None key, so 'unanswerable' must not become 'unset' (G24-22)."""
    monkeypatch.setattr(c, "is_abs_dir", lambda p, t: True)
    monkeypatch.setattr(c, "is_abs_file", lambda p, t: True)
    assert pinned().shapes_ok(c.Platform.WINDOWS) is True
    for field in ("system_root", "com_spec", "appdata", "home_path", "home", "temp", "tmp"):
        assert pinned(**{field: None}).shapes_ok(c.Platform.WINDOWS) is False, field
    # The WOW64 trio is genuinely absent on 32-bit Windows — absence there is a platform fact, not an unanswered question.
    for field in ("program_files_x86", "program_w6432", "common_program_files_x86"):
        assert pinned(**{field: None}).shapes_ok(c.Platform.WINDOWS) is True, field


def test_posix_requires_tmpdir_and_forbids_the_windows_fields(monkeypatch):
    monkeypatch.setattr(c, "is_abs_dir", lambda p, t: True)
    monkeypatch.setattr(c, "is_abs_file", lambda p, t: True)
    posix_fields = {k: None for k in (
        "system_root", "windir", "system_drive", "program_data", "program_files", "common_program_files",
        "all_users_profile", "public", "com_spec", "user_profile", "home_drive", "home_path", "appdata",
        "local_appdata")}
    assert pinned(tmpdir=c.AbsDir("/tmp"), **posix_fields).shapes_ok(c.Platform.POSIX) is True
    assert pinned(tmpdir=None, **posix_fields).shapes_ok(c.Platform.POSIX) is False
    assert pinned(tmpdir=c.AbsDir("/tmp")).shapes_ok(c.Platform.POSIX) is False  # Windows fields still populated


def test_the_public_profile_is_not_a_trusted_system_path():
    """ENV-06g: `C:\\Users\\Public` is writable by design; requiring IMG-01 of it rejects every enabled rung."""
    paths = pinned().system_paths()
    assert r"C:\Users\Public" not in paths
    assert r"C:\Windows" in paths and r"C:\ProgramData" in paths


# ------------------------------------------------------------------ LAUNCH: encoding and measuring


@pytest.mark.parametrize("quote", ["\u2018", "\u2019", "\u201a", "\u201b"])
def test_powershell_workdir_refuses_the_non_ascii_single_quotes(quote):
    """LAUNCH-09e: PowerShell's tokenizer has five single-quote delimiters; doubling only covers the ASCII one."""
    assert c.encode_workdir(c.AbsPath(f"C:\\{quote}x"), c.ShellDialect.POWERSHELL) is None


def test_powershell_workdir_doubles_the_ascii_apostrophe():
    assert c.encode_workdir(c.AbsPath(r"C:\it's"), c.ShellDialect.POWERSHELL) == r"C:\it''s"
    assert c.encode_workdir(c.AbsPath(r"C:\plain"), c.ShellDialect.POWERSHELL) == r"C:\plain"


def test_the_adversarial_workdir_from_the_review_is_refused():
    hostile = c.AbsPath("C:\\\u2019; Start-Process calc; Write-Output \u2018")
    assert c.encode_workdir(hostile, c.ShellDialect.POWERSHELL) is None


@pytest.mark.parametrize("ch", ['"', "%", "^", "&", "|", "<", ">", "\r", "\n"])
def test_cmd_workdir_refuses_its_metacharacters_and_newlines(ch):
    """LAUNCH-09b: a newline in a `/c` string splits the command line outside the analysed structure."""
    assert c.encode_workdir(c.AbsPath(f"C:\\a{ch}b"), c.ShellDialect.CMD) is None


def test_posix_workdir_escapes_inside_a_single_quoted_literal():
    assert c.encode_workdir(c.AbsPath("/home/it's"), c.ShellDialect.POSIX) == "/home/it'\\''s"


@pytest.mark.parametrize("dialect", [c.ShellDialect.POWERSHELL, c.ShellDialect.POSIX, c.ShellDialect.CMD])
def test_no_dialect_encodes_a_nul_or_a_lone_surrogate(dialect):
    assert c.encode_workdir(c.AbsPath("C:\\a\x00b"), dialect) is None
    assert c.encode_workdir(c.AbsPath("C:\\a\ud800b"), dialect) is None


def test_lone_surrogates_are_detected_and_astral_characters_are_not():
    assert c.has_lone_surrogate("echo \ud800") is True
    assert c.has_lone_surrogate("echo \udfff") is True
    assert c.has_lone_surrogate("emoji \U0001F600") is False
    assert c.has_lone_surrogate("plain") is False


def test_the_floor_answers_a_lone_surrogate_with_a_verdict_not_an_exception():
    """LAUNCH-08e / method rule 22: the three measurements encode first, and an exception is not a DENY."""
    out = c.floor(spec(), "echo \ud800", c.AbsPath(r"C:\repo"), {"PATH": ""}, ())
    assert isinstance(out.verdict, c.Deny) and out.verdict.reason.endswith(":lone-surrogate")
    env_out = c.floor(spec(), "echo ok", c.AbsPath(r"C:\repo"), {"X": "\ud800"}, ())
    assert isinstance(env_out.verdict, c.Deny) and env_out.verdict.reason.endswith(":lone-surrogate")


def test_command_line_units_count_utf16(monkeypatch):
    assert c.createprocess_units("ab") == 3  # two units plus the trailing NUL
    assert c.cmd_line_chars("ab") == 2
    assert c.cmd_line_chars("\U0001F600") == 2  # LAUNCH-08a: a non-BMP character counts 2


# ------------------------------------------------------------------ SPEC: cross-invariants and the decided record


def test_validate_holds_the_policy_cross_invariants():
    assert c.validate(spec()) is None
    assert c.validate(spec(dialect=c.ShellDialect.UNKNOWN)) == "hardline:unknown-dialect-opaque"
    assert c.validate(spec(rung=c.Rung.pwsh)) == "hardline:unknown-rung-opaque"  # CMD × pwsh is not a legal pair
    assert c.validate(spec(policy_enabled=False)) == "hardline:unknown-rung-opaque"
    assert c.validate(spec(launcher=None)) == "hardline:unknown-rung-opaque"
    assert c.validate(spec(pinned_env=None)) == "hardline:unknown-rung-opaque"


def test_an_explicit_shell_on_a_policy_enabled_rung_is_refused():
    """CFG-02c: the launcher decides which executable runs; a second answer is a second source."""
    assert c.validate(spec(explicit_shell=c.AbsPath("/bin/zsh"))) == "hardline:unknown-rung-opaque"


def test_the_powershell_rungs_require_an_interpreter_identity():
    """IMG-07: `pwsh` needs the measured `(edition, version)`, which a bare LauncherIdentity cannot carry."""
    ps = dict(dialect=c.ShellDialect.POWERSHELL, rung=c.Rung.pwsh)
    assert c.validate(spec(launcher=interpreter(), **ps)) is None
    assert c.validate(spec(launcher=launcher(), **ps)) == "hardline:unknown-rung-opaque"


def test_deciding_voids_the_previous_record_before_any_early_return():
    """SPEC-08c: an early DENY must not leave the *previous* call's body and environment for `launch()`."""
    plan = c.ToolCallPlan(decided=c.DecidedCall(
        spec=spec(), body="Get-Date", cwd=c.AbsPath(r"C:\repo"), verdict=c.PASS, child_env={}, attested_images=()))

    class Provider:
        shell_spec = c.Exhausted("every rung refused")

    verdict = c.decide(Provider(), "Remove-Item C:\\", c.AbsPath(r"C:\other"), plan)
    assert isinstance(verdict, c.Deny)
    assert plan.decided is None
    assert isinstance(c.launch(plan, Provider()), c.Deny)


def test_launch_refuses_a_record_whose_verdict_was_deny():
    """SPEC-08b: a record being present is not the same as this call having been allowed."""
    s = spec()
    plan = c.ToolCallPlan(decided=c.DecidedCall(
        spec=s, body="rm -rf /", cwd=c.AbsPath(r"C:\repo"), verdict=c.Deny("hardline:destructive"),
        child_env={}, attested_images=()))

    class Provider:
        shell_spec = s

    assert c.launch(plan, Provider()) == c.Deny("hardline:destructive")


def test_launch_refuses_when_the_spec_was_reparsed_between_decide_and_launch():
    plan = c.ToolCallPlan(decided=c.DecidedCall(
        spec=spec(), body="Get-Date", cwd=c.AbsPath(r"C:\repo"), verdict=c.PASS, child_env={}, attested_images=()))

    class Provider:
        shell_spec = spec()  # an equal value, a different object

    out = c.launch(plan, Provider())
    assert isinstance(out, c.Deny) and out.reason.endswith(":launch-spec-changed")


def test_merge_images_fails_closed_on_conflicting_attestations():
    """LAUNCH-01d: keeping the first of two disagreeing records hands the executor a stale identity."""
    assert c.merge_images((image(sha="aa"), image(sha="aa"))) == (image(sha="aa"),)
    assert c.merge_images((image(sha="aa"), image(sha="bb"))) is None


def test_the_fingerprint_projection_separates_configs_that_differ_only_in_policy():
    """IMG-03a and CFG-02c: both values are decided against, so both must reach the fingerprint (SPEC-08)."""
    base = c.fingerprint_projection(spec())
    pin = (c.HashPin(path=c.AbsPath(r"C:\Prog\pwsh.exe"), sha256=c.Sha256("aa")),)
    assert c.fingerprint_projection(spec(allowlist=pin)) != base
    legacy = spec(rung=c.Rung.system_posix, dialect=c.ShellDialect.POSIX, policy_enabled=False,
                  launcher=None, pinned_env=None, target_platform=c.Platform.POSIX)
    assert c.fingerprint_projection(legacy.__class__(**{**legacy.__dict__, "explicit_shell": c.AbsPath("/bin/zsh")})) \
        != c.fingerprint_projection(legacy)


# ------------------------------------------------------------------ SPEC-05c / LADDER: oracle completeness


def test_oracle_methods_lists_every_protocol_method():
    """SPEC-05c: `oracle_complete` and G24-11 are both parameterised by `ORACLE_METHODS`, so a method added
    to `IdentityOracle` and not to that tuple drops out of the completeness gate — silently, and with the
    stub-vs-tuple assertion above still green because the stub would not have grown it either."""
    declared = {n for n in vars(c.IdentityOracle) if not n.startswith("_")}
    assert declared == set(c.ORACLE_METHODS)


def test_oracle_completeness_is_every_method_not_just_presence():
    assert c.oracle_complete(oracle()) is True
    assert c.oracle_complete(None) is False
    for method in c.ORACLE_METHODS:
        assert c.oracle_complete(oracle(omit=(method,))) is False, method


def test_an_unanswerable_locality_reads_as_false_rather_than_refusing():
    """SPEC-04a: absent and unanswerable both read `false`, which is the stricter side — not an empty ladder."""
    assert c.target_is_local(oracle()) is True
    assert c.target_is_local(oracle(target_filesystem_is_local=False)) is False
    assert c.target_is_local(oracle(omit=("target_filesystem_is_local",))) is False
    # SPEC-04a names two unanswerable states, not one: the method absent, and the method present and
    # unable to answer. `-> bool` could only express the first, so the second had to raise — out of
    # `select_rung` entirely, before either policy-off rung could be chosen.
    assert c.target_is_local(oracle(target_filesystem_is_local=None)) is False


@pytest.mark.parametrize("omitted", ["target_filesystem_is_local", "preflight", "discover", "image_signer"])
def test_an_incomplete_oracle_still_selects_the_policy_off_rungs(monkeypatch, omitted):
    """SPEC-05c's last clause: only the policy-off rungs keep running, and they never consult an oracle."""
    monkeypatch.setattr(c, "fingerprint_of", lambda projection: c.Sha256("fp"))
    posix = c.select_rung(c.ShellBlock(), oracle(omit=(omitted,), target_platform=c.Platform.POSIX), c.Subject("subj"))
    assert isinstance(posix, c.ShellSpec) and posix.rung is c.Rung.system_posix and posix.policy_enabled is False
    windows = c.select_rung(c.ShellBlock(), oracle(omit=(omitted,)), c.Subject("subj"))
    assert isinstance(windows, c.ShellSpec) and windows.rung is c.Rung.legacy_cmd


def test_selection_still_needs_the_target_platform():
    out = c.select_rung(c.ShellBlock(), oracle(omit=("target_platform",)), c.Subject("subj"))
    assert isinstance(out, c.Exhausted)


def test_the_ladder_itself_refuses_an_incomplete_oracle(monkeypatch):
    monkeypatch.setattr(c, "LADDER_FLIPPED", True)
    out = c.select_rung(c.ShellBlock(), oracle(omit=("preflight",)), c.Subject("subj"))
    assert isinstance(out, c.Exhausted) and "SPEC-05c" in out.reason


def test_an_explicit_posix_shell_survives_its_policy_off_rung(monkeypatch):
    """CFG-02c: `/bin/bash` and `/bin/zsh` must not produce the same spec and the same fingerprint."""
    monkeypatch.setattr(c, "fingerprint_of", lambda projection: c.Sha256(repr(projection)))
    monkeypatch.setattr(c, "ancestors_to_volume_root",
                        lambda p, target=None: tuple(p.rsplit("/", 1)[:1]) or ("/",))
    monkeypatch.setattr(c, "path_within", lambda path, root, target: path.startswith(root.rstrip("/") + "/"))

    def build(path):
        o = oracle(target_platform=c.Platform.POSIX,
                   target_project_root=c.AbsPath("/repo"),
                   resolve_image=lambda self, p, subject: image(p),
                   read_identity=lambda self, img, dialect: c.LauncherIdentity(image=img, launcher_hash=c.Sha256("aa")))
        return c.select_rung(c.ShellBlock(path=c.AbsPath(path), dialect=c.ShellDialect.POSIX), o, c.Subject("subj"))

    bash, zsh = build("/bin/bash"), build("/bin/zsh")
    assert isinstance(bash, c.ShellSpec) and bash.rung is c.Rung.system_posix and bash.policy_enabled is False
    assert bash.explicit_shell == "/bin/bash" and zsh.explicit_shell == "/bin/zsh"
    assert bash.fingerprint != zsh.fingerprint


def test_an_explicit_launcher_inside_the_project_root_is_refused(monkeypatch):
    """IMG-05a: passing the trust-root chain is not the same as being outside the worktree (G25-06)."""
    monkeypatch.setattr(c, "ancestors_to_volume_root", _chain)
    monkeypatch.setattr(c, "path_within", lambda path, root, target: path.startswith(root))
    o = oracle(target_project_root=c.AbsPath(r"C:\repo"),
               resolve_image=lambda self, p, subject: image(p))
    out = c.select_rung(c.ShellBlock(path=c.AbsPath(r"C:\repo\vendor\pwsh.exe"), dialect=c.ShellDialect.POWERSHELL),
                        o, c.Subject("subj"))
    assert isinstance(out, c.Exhausted) and "IMG-05a" in out.reason


def test_an_unanswerable_project_root_refuses_an_explicit_launcher(monkeypatch):
    monkeypatch.setattr(c, "ancestors_to_volume_root", _chain)
    monkeypatch.setattr(c, "path_within", lambda path, root, target: False)
    o = oracle(target_project_root=None, resolve_image=lambda self, p, subject: image(p))
    out = c.select_rung(c.ShellBlock(path=c.AbsPath(r"C:\Prog\pwsh.exe"), dialect=c.ShellDialect.POWERSHELL),
                        o, c.Subject("subj"))
    assert isinstance(out, c.Exhausted) and "IMG-05a" in out.reason


def test_a_shell_block_naming_only_one_of_path_and_dialect_is_refused():
    for block in (c.ShellBlock(path=c.AbsPath(r"C:\Prog\pwsh.exe")), c.ShellBlock(dialect=c.ShellDialect.POWERSHELL)):
        out = c.select_rung(block, oracle(), c.Subject("subj"))
        assert isinstance(out, c.Exhausted) and "path / dialect" in out.reason


def test_derive_rung_reads_the_target_platform_not_the_host():
    """CFG-02a: every row of the fixed table reads the target platform, the `cmd` row included.

    `cmd` on a POSIX target must refuse the source, not derive a policy-enabled `cmd` spec: cmd.exe does
    not exist there, none of `floor`'s three measurements applies to it, and every call would then be
    denied for `launch-oversize` — a length reason for a platform error.
    """
    assert c.derive_rung(c.ShellDialect.POSIX, c.Platform.WINDOWS, None) is c.Rung.git_bash
    assert c.derive_rung(c.ShellDialect.POSIX, c.Platform.POSIX, None) is c.Rung.system_posix
    assert c.derive_rung(c.ShellDialect.CMD, c.Platform.WINDOWS, None) is c.Rung.cmd
    assert c.derive_rung(c.ShellDialect.CMD, c.Platform.POSIX, None) is None
    assert c.derive_rung(c.ShellDialect.POWERSHELL, c.Platform.WINDOWS, None) is None  # no identity, no edition
    assert c.derive_rung(c.ShellDialect.UNKNOWN, c.Platform.WINDOWS, None) is None
