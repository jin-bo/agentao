"""Trusted roots, the child environment, the launch matrix and the ladder.

PR-4 of the PowerShell ladder. ``IMG-01``..``IMG-09``, ``ENV-01``..``ENV-06``,
``LAUNCH-02``..``LAUNCH-09``, ``LADDER-01``..``LADDER-03``, ``SPEC-04a`` and ``SPEC-05`` are
defined once each in ``docs/design/powershell-support-spec.zh.md`` §2.
"""

from __future__ import annotations

import pytest

from agentao.capabilities.shell_spec import (
    AbsDir,
    AbsFile,
    AbsPath,
    DriveSpec,
    Exhausted,
    HashPin,
    Platform,
    PosixLaunch,
    PublisherTrust,
    RootRelPath,
    Rung,
    Sha256,
    ShellBlock,
    ShellDialect,
    ShellSpec,
    Subject,
    WindowsLaunch,
    legacy_spec,
)
from agentao.permissions_hardline._trust import (
    CMD_MAX_CHARS,
    CREATEPROCESS_MAX_UNITS,
    EnvInputs,
    ORACLE_METHODS,
    ReparseResult,
    ReparseState,
    ancestors_to_volume_root,
    attested_spec,
    child_env,
    cmd_line_chars,
    createprocess_units,
    derive_rung,
    encode_workdir,
    filtered_path_entries,
    has_lone_surrogate,
    host_identity_ok,
    oracle_complete,
    oversize_reason,
    path_within,
    prelude_for,
    read_env_inputs,
    resolve,
    resolve_name,
    request_for,
    select_rung,
    target_is_local,
    trusted_root_chain,
)

from ._trust_fakes import (
    SUBJECT,
    FakeOracle,
    image,
    interpreter,
    windows_pinned,
)

PWSH = "C:\\Program Files\\PowerShell\\7\\pwsh.exe"


def ladder_oracle(**kwargs) -> FakeOracle:
    """An oracle that would select ``pwsh`` if the ladder ran."""
    img = image(PWSH)
    defaults = dict(
        discovered={Rung.pwsh: img},
        identities={PWSH: interpreter(PWSH, img=img)},
        trusted_publishers={PWSH},
        pshome=AbsPath("C:\\Program Files\\PowerShell\\7"),
    )
    defaults.update(kwargs)
    return FakeOracle(**defaults)


# ------------------------------------------------------------------ IMG-01


def test_the_chain_walks_every_ancestor_to_the_volume_root():
    """IMG-01 asks about the image *and* every directory above it.

    A writable parent is a replaceable child: nothing about the file's own ACL prevents
    somebody who can rename its directory from putting a different tree there.
    """
    assert ancestors_to_volume_root(AbsPath("C:\\a\\b\\c.exe"), Platform.WINDOWS) == (
        AbsPath("C:\\a\\b"), AbsPath("C:\\a"), AbsPath("C:\\")
    )
    assert ancestors_to_volume_root(AbsPath("/usr/local/bin/git"), Platform.POSIX) == (
        AbsPath("/usr/local/bin"), AbsPath("/usr/local"), AbsPath("/usr"), AbsPath("/")
    )


def test_a_writable_ancestor_fails_the_chain_even_when_the_file_is_locked_down():
    oracle = FakeOracle(writable={"C:\\a"})
    assert trusted_root_chain(
        AbsPath("C:\\a\\b\\c.exe"), SUBJECT, oracle, Platform.WINDOWS
    ) is False


def test_every_candidate_root_writable_leaves_no_trusted_set():
    """G25-04 / LADDER-03: the rung is refused rather than downgraded."""
    oracle = ladder_oracle(writable={PWSH})
    spec = attested_spec(
        Rung.pwsh, image(PWSH), interpreter(PWSH), ShellBlock(), oracle,
        Platform.WINDOWS, SUBJECT, True,
    )
    assert isinstance(spec, Exhausted)


def test_containment_compares_segments_and_not_string_prefixes():
    """``C:\\repo-evil`` starts with ``C:\\repo`` and is not inside it."""
    assert path_within(AbsPath("C:\\repo\\src"), AbsPath("C:\\repo"), Platform.WINDOWS)
    assert not path_within(AbsPath("C:\\repo-evil"), AbsPath("C:\\repo"), Platform.WINDOWS)
    assert path_within(AbsPath("C:/REPO/src"), AbsPath("c:\\repo"), Platform.WINDOWS)
    assert not path_within(AbsPath("/repo-evil"), AbsPath("/repo"), Platform.POSIX)
    assert not path_within(AbsPath("/REPO/src"), AbsPath("/repo"), Platform.POSIX)


# ------------------------------------------------------------------ IMG-06c


def test_a_reparse_that_cannot_be_read_is_not_a_path_that_is_not_a_reparse():
    """G23-10: collapsing "failed" into "not one" walks an unexamined chain as examined."""
    oracle = FakeOracle(reparse={"C:\\a\\b": ReparseResult(ReparseState.error)})
    assert trusted_root_chain(
        AbsPath("C:\\a\\b\\c.exe"), SUBJECT, oracle, Platform.WINDOWS
    ) is False


def test_a_junction_cycle_returns_rather_than_recursing_forever():
    oracle = FakeOracle(reparse={
        "C:\\a": ReparseResult(ReparseState.resolved, AbsPath("C:\\b")),
        "C:\\b": ReparseResult(ReparseState.resolved, AbsPath("C:\\a")),
    })
    assert trusted_root_chain(AbsPath("C:\\a"), SUBJECT, oracle, Platform.WINDOWS) is False


def test_a_chain_deeper_than_the_ceiling_is_refused():
    depth = 40
    reparse = {
        f"C:\\d{i}": ReparseResult(ReparseState.resolved, AbsPath(f"C:\\d{i + 1}"))
        for i in range(depth)
    }
    oracle = FakeOracle(reparse=reparse)
    assert trusted_root_chain(AbsPath("C:\\d0"), SUBJECT, oracle, Platform.WINDOWS) is False


def test_a_junction_pointing_at_its_own_parent_stays_trusted():
    r"""G23-10's fourth case, and the reason ``following`` is per-walk.

    ``C:\Trusted\alias`` -> ``C:\Trusted`` resolves, and every question on the chain passes.
    Reusing one "already visited" set would meet the parent — which entered while checking the
    alias — and call a working chain a cycle, excluding it from launcher selection and PATH.
    """
    oracle = FakeOracle(reparse={
        "C:\\Trusted\\alias": ReparseResult(ReparseState.resolved, AbsPath("C:\\Trusted")),
    })
    assert trusted_root_chain(
        AbsPath("C:\\Trusted\\alias"), SUBJECT, oracle, Platform.WINDOWS
    ) is True


# ------------------------------------------------------------------ IMG-03 / IMG-03b


def test_a_named_signer_and_the_hosts_own_store_are_two_different_questions():
    """G23-13: without ``image_signer`` a ``PublisherTrust`` entry has nowhere to be read.

    Adding a trusted publisher to the allowlist would then be identical to leaving it empty,
    while IMG-03 lists it as one of the allowlist's two forms.
    """
    img = image(PWSH)
    allowlist = (PublisherTrust(signer="CN=Contoso"),)
    signed = FakeOracle(signers={PWSH: "CN=Contoso"})
    other = FakeOracle(signers={PWSH: "CN=Someone Else"})
    unsigned = FakeOracle()
    assert host_identity_ok(img, allowlist, signed) is True
    assert host_identity_ok(img, allowlist, other) is False
    assert host_identity_ok(img, allowlist, unsigned) is False
    assert host_identity_ok(img, (), signed) is False
    assert host_identity_ok(img, (), FakeOracle(trusted_publishers={PWSH})) is True


def test_the_pin_lookup_and_the_pin_itself_compare_paths_the_same_way():
    r"""IMG-03 / IMG-06b: both sides are canonical, so one comparison rule serves both.

    Looking a pin up loosely and confirming it strictly would be two answers to one question:
    a pin spelled ``c:\pwsh.exe`` would be *found* for ``C:\pwsh.exe`` and then fail to
    match, turning "this image is unpinned" into "this image is untrusted".
    """
    img = image(PWSH, sha="bb")
    exact = HashPin(path=AbsPath(PWSH), sha256=Sha256("bb"))
    wrong_case = HashPin(path=AbsPath(PWSH.lower()), sha256=Sha256("bb"))
    assert host_identity_ok(img, (exact,), FakeOracle()) is True
    assert host_identity_ok(img, (wrong_case,), FakeOracle()) is False
    from agentao.permissions_hardline._trust import trusted_image

    assert trusted_image(img, SUBJECT, (wrong_case,), FakeOracle(), Platform.WINDOWS) is True


# ------------------------------------------------------------------ ENV-01


def test_the_path_filter_normalises_before_it_compares():
    """G04-04: ``/usr/local/../home/me/bin`` and ``/home/me/bin`` are one directory.

    A PATH entry is a raw string out of an environment and ``path_within`` compares canonical
    paths, so without this step ``..``, an 8.3 short name or a symlink walks past both
    containment tests. Both spellings have to be gone, not just the obvious one.
    """
    oracle = FakeOracle(
        target=Platform.POSIX,
        canonical={"/usr/local/../home/me/bin": "/home/me/bin"},
        writable={"/home/me/bin"},
    )
    entries = (
        AbsPath("/usr/bin"),
        AbsPath("/home/me/bin"),
        AbsPath("/usr/local/../home/me/bin"),
    )
    kept = filtered_path_entries(
        SUBJECT, entries, AbsPath("/work"), AbsPath("/repo"), Platform.POSIX, oracle
    )
    assert kept == (AbsPath("/usr/bin"),)


def test_entries_inside_the_working_directory_or_the_project_root_are_dropped():
    oracle = FakeOracle(target=Platform.POSIX)
    entries = (
        AbsPath("/usr/bin"), AbsPath("/work/bin"), AbsPath("/repo/tools"), AbsPath("rel/bin"),
        AbsPath(""),
    )
    kept = filtered_path_entries(
        SUBJECT, entries, AbsPath("/work"), AbsPath("/repo"), Platform.POSIX, oracle
    )
    assert kept == (AbsPath("/usr/bin"),)


def test_an_entry_the_oracle_cannot_canonicalise_does_not_reach_the_child():
    oracle = FakeOracle(target=Platform.POSIX, canonical={"/weird": ""})
    kept = filtered_path_entries(
        SUBJECT, (AbsPath("/weird"),), AbsPath("/work"), AbsPath("/repo"),
        Platform.POSIX, oracle,
    )
    assert kept == ()


# ------------------------------------------------------------------ ENV-06


def policy_spec(**kwargs) -> ShellSpec:
    """A policy-on spec built directly, so the environment tests do not run the whole ladder."""
    oracle = kwargs.pop("oracle", None) or ladder_oracle()
    spec = attested_spec(
        Rung.pwsh, image(PWSH), interpreter(PWSH), ShellBlock(**kwargs), oracle,
        Platform.WINDOWS, SUBJECT, True,
    )
    assert isinstance(spec, ShellSpec), spec
    return spec


def inputs_with(base) -> EnvInputs:
    return EnvInputs(
        base=base, path_entries=(), cwd=AbsPath("C:\\work"), project_root=AbsPath("C:\\repo")
    )


def test_the_child_environment_is_a_closed_set_in_three_groups():
    """G18-08 / G18-10: everything that hands "where to read config" to the environment goes.

    The effect table measures the command line, so a command-line-inert trusted program is
    handed code through exactly these keys.
    """
    spec = policy_spec()
    env = child_env(spec, windows_pinned(), inputs_with({
        "GIT_CONFIG_GLOBAL": "C:\\evil\\config",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.pager",
        "NODE_OPTIONS": "--require C:\\evil\\x.js",
        "PYTHONPATH": "C:\\evil",
        "LD_PRELOAD": "/evil/x.so",
        "XDG_CONFIG_HOME": "C:\\evil",
        "SSL_CERT_FILE": "C:\\evil\\ca.pem",
        "SHELLOPTS": "xtrace",
        "BASHOPTS": "checkwinsize",
        "USERNAME": "me",
    }), ())
    for key in (
        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "NODE_OPTIONS",
        "PYTHONPATH", "LD_PRELOAD", "XDG_CONFIG_HOME", "SSL_CERT_FILE", "SHELLOPTS",
        "BASHOPTS",
    ):
        assert key not in env, key
    assert env["USERNAME"] == "me"
    assert env["PATHEXT"] == ".COM;.EXE"
    assert env["SystemRoot"] == "C:\\Windows"


def test_a_reserved_key_cannot_be_granted_back_and_a_star_grant_is_dropped_whole():
    """G18-16 / ENV-06d: the unit of authorisation is a literal key name.

    One ``*`` puts the entire inherited environment back, which is the chain this rule closes.
    """
    base = {"BASH_ENV": "C:\\evil\\rc", "GIT_CONFIG_GLOBAL": "C:\\evil", "JAVA_HOME": "C:\\jdk"}
    star = child_env(policy_spec(env_passthrough=("*",)), windows_pinned(), inputs_with(base), ())
    prefix = child_env(
        policy_spec(env_passthrough=("GIT_*",)), windows_pinned(), inputs_with(base), ()
    )
    reserved = child_env(
        policy_spec(env_passthrough=("BASH_ENV",)), windows_pinned(), inputs_with(base), ()
    )
    named = child_env(
        policy_spec(env_passthrough=("JAVA_HOME",)), windows_pinned(), inputs_with(base), ()
    )
    assert "GIT_CONFIG_GLOBAL" not in star and "BASH_ENV" not in star
    assert "GIT_CONFIG_GLOBAL" not in prefix
    assert "BASH_ENV" not in reserved
    assert named["JAVA_HOME"] == "C:\\jdk"


def test_a_granted_value_pointing_into_the_work_tree_is_removed_not_rewritten():
    spec = policy_spec(env_passthrough=("JAVA_HOME",))
    inside = child_env(
        spec, windows_pinned(), inputs_with({"JAVA_HOME": "C:\\repo\\jdk"}), ()
    )
    relative = child_env(spec, windows_pinned(), inputs_with({"JAVA_HOME": ".\\jdk"}), ())
    assert "JAVA_HOME" not in inside
    assert "JAVA_HOME" not in relative


def test_a_descriptive_key_carrying_a_path_fails_its_registered_shape():
    spec = policy_spec()
    env = child_env(
        spec, windows_pinned(), inputs_with({"USERNAME": "C:\\Users\\me", "TERM": "xterm"}), ()
    )
    assert "USERNAME" not in env and env["TERM"] == "xterm"


def test_windows_folds_keys_before_the_set_arithmetic_and_a_split_collision_drops():
    """ENV-06e: ``Path`` and ``PATH`` are one key; two different values must not race."""
    spec = policy_spec(env_passthrough=("JAVA_HOME",))
    env = child_env(
        spec, windows_pinned(),
        inputs_with({"java_home": "C:\\a", "JAVA_HOME": "C:\\b"}), (),
    )
    assert "JAVA_HOME" not in env


def test_the_pinned_values_come_from_the_record_and_never_from_the_base_environment():
    spec = policy_spec()
    env = child_env(
        spec, windows_pinned(), inputs_with({"SystemRoot": "C:\\evil", "TEMP": "C:\\evil"}), ()
    )
    assert env["SystemRoot"] == "C:\\Windows"
    assert env["TEMP"].endswith("Temp")


def test_the_child_path_is_the_sequence_decide_already_filtered():
    """ENV-01a: one filter per call, and the environment writes down its result."""
    spec = policy_spec()
    env = child_env(
        spec, windows_pinned(), inputs_with({}),
        (AbsPath("C:\\Windows\\System32"), AbsPath("C:\\Program Files\\Git\\cmd")),
    )
    assert env["PATH"] == "C:\\Windows\\System32;C:\\Program Files\\Git\\cmd"


# ------------------------------------------------------------------ ENV-06b / ENV-06f


def test_an_unregistered_key_or_a_wrong_shape_refuses_the_rung():
    """G14-05: four separate refusals, and a writable *profile* directory is not one of them."""
    oracle = ladder_oracle()
    block = ShellBlock()

    def build(pinned) -> object:
        return attested_spec(
            Rung.pwsh, image(PWSH), interpreter(PWSH), block,
            ladder_oracle(pinned=pinned), Platform.WINDOWS, SUBJECT, True,
        )

    assert isinstance(build(windows_pinned(unknown_keys=frozenset({"EXTRA"}))), Exhausted)
    assert isinstance(build(windows_pinned(system_drive=DriveSpec("C:\\"))), Exhausted)
    assert isinstance(build(windows_pinned(home_path=RootRelPath("C:\\Users\\me"))), Exhausted)
    assert isinstance(build(windows_pinned(com_spec=AbsFile("C:\\Windows\\System32\\"))), Exhausted)
    # A system directory the subject can write refuses; the profile group does not.
    system_writable = ladder_oracle(writable={"C:\\ProgramData"})
    assert isinstance(
        attested_spec(
            Rung.pwsh, image(PWSH), interpreter(PWSH), block, system_writable,
            Platform.WINDOWS, SUBJECT, True,
        ),
        Exhausted,
    )
    profile_writable = ladder_oracle(writable={"C:\\Users\\me"})
    assert isinstance(
        attested_spec(
            Rung.pwsh, image(PWSH), interpreter(PWSH), block, profile_writable,
            Platform.WINDOWS, SUBJECT, True,
        ),
        ShellSpec,
    )
    del oracle


@pytest.mark.parametrize(
    "field",
    [
        "system_root", "windir", "system_drive", "program_data", "program_files",
        "common_program_files", "all_users_profile", "public", "com_spec", "user_profile",
        "home_drive", "home_path", "appdata", "local_appdata",
    ],
)
def test_every_required_windows_field_is_required(field):
    """G24-22: an unanswered field is not "one key fewer", it is an unvalidated environment.

    ``child_env`` renders ``None`` as "this key does not appear", so silence here would ship a
    child environment nobody checked — ENV-06a's reason, word for word.
    """
    assert not windows_pinned(**{field: None}).shapes_ok(Platform.WINDOWS)


def test_the_three_wow64_keys_may_be_absent_because_they_do_not_exist_on_32_bit():
    """G24-22's other half: missing is a fact about the platform, not an unanswered question."""
    assert windows_pinned().shapes_ok(Platform.WINDOWS)


def test_a_posix_target_must_answer_tmpdir():
    posix = dict(home=AbsDir("/home/me"), temp=AbsDir("/tmp"), tmp=AbsDir("/tmp"))
    from agentao.capabilities.shell_spec import PinnedEnv

    assert PinnedEnv(tmpdir=AbsDir("/tmp"), **posix).shapes_ok(Platform.POSIX)
    assert not PinnedEnv(**posix).shapes_ok(Platform.POSIX)


def test_public_is_not_in_the_group_that_must_pass_img_01():
    r"""ENV-06g: ``C:\Users\Public`` is writable by everyone by design and nothing loads it.

    Putting it in the system group would let one make-weight key refuse every policy-on rung.
    """
    assert AbsPath("C:\\Users\\Public") not in windows_pinned().system_paths()


# ------------------------------------------------------------------ LAUNCH-08


def test_a_lone_surrogate_is_refused_before_anything_encodes_it():
    """G24-23: the alternative is ``UnicodeEncodeError``, and an exception is not a verdict."""
    assert has_lone_surrogate("a\ud800b")
    assert not has_lone_surrogate("a\U0001F600b")  # a paired non-BMP character is fine


def test_the_windows_measure_counts_utf16_units_and_the_terminating_nul():
    assert createprocess_units("") == 1
    assert createprocess_units("abc") == 4
    assert createprocess_units("\U0001F600") == 3  # non-BMP counts two, plus the NUL
    assert cmd_line_chars("\U0001F600") == 2


def test_an_over_long_command_line_is_refused_and_never_truncated():
    spec = policy_spec()
    body = "x" * (CREATEPROCESS_MAX_UNITS + 10)
    request = request_for(
        spec, spec.launcher, body, "C:\\work", {}, AbsPath("C:\\work"), ()
    )
    assert oversize_reason(spec, request) == "launch-oversize"


def test_the_cmd_rung_measures_its_own_limit_and_its_own_environment_entries():
    """LAUNCH-08b: two measurements at once, whichever trips first, and never summed."""
    oracle = ladder_oracle()
    spec = attested_spec(
        Rung.cmd, image("C:\\Windows\\System32\\cmd.exe"),
        interpreter("C:\\Windows\\System32\\cmd.exe"), ShellBlock(), oracle,
        Platform.WINDOWS, SUBJECT, True,
    )
    assert isinstance(spec, ShellSpec)
    long_env = {"BIG": "y" * (CMD_MAX_CHARS + 1)}
    request = request_for(spec, spec.launcher, "dir", "C:\\work", long_env, AbsPath("C:\\work"), ())
    assert oversize_reason(spec, request) == "launch-env-oversize"
    over = request_for(
        spec, spec.launcher, "d" * (CMD_MAX_CHARS + 1), "C:\\work", {}, AbsPath("C:\\work"), ()
    )
    assert oversize_reason(spec, over) == "launch-oversize"


def test_a_policy_off_rung_is_not_measured_at_all():
    """LAUNCH-08 constrains policy-on rungs only — LADDER-05 promises the rest is unchanged."""
    spec = legacy_spec(ShellDialect.CMD, Rung.legacy_cmd, Platform.WINDOWS, SUBJECT, True)
    request = WindowsLaunch(
        application_name=AbsPath("C:\\Windows\\System32\\cmd.exe"),
        command_line="x" * 100000, cwd=AbsPath("C:\\"), workdir=AbsPath("C:\\"),
        env={}, execution_subject=SUBJECT, attested_images=(), spec_fingerprint=Sha256(""),
    )
    assert oversize_reason(spec, request) is None


# ------------------------------------------------------------------ LAUNCH-09


@pytest.mark.parametrize("quote", ["\u2018", "\u2019", "\u201a", "\u201b"])
def test_powershell_refuses_the_four_typographic_single_quotes(quote):
    r"""G21-20: they close a single-quoted literal, and the prelude is text the floor never scans.

    ``C:\’; Start-Process calc; Write-Output ‘`` splices into the *prelude*. Whether doubling
    escapes them is unmeasured, so the answer is the refusing side.
    """
    assert encode_workdir(AbsPath(f"C:\\dir{quote}x"), ShellDialect.POWERSHELL) is None


def test_the_adversarial_working_directory_is_refused_and_a_plain_apostrophe_is_doubled():
    hostile = AbsPath("C:\\\u2019; Start-Process calc; Write-Output \u2018")
    assert encode_workdir(hostile, ShellDialect.POWERSHELL) is None
    assert encode_workdir(AbsPath("C:\\it's"), ShellDialect.POWERSHELL) == "C:\\it''s"
    assert encode_workdir(AbsPath("/it's"), ShellDialect.POSIX) == "/it'\\''s"


@pytest.mark.parametrize("char", ['"', "%", "^", "&", "|", "<", ">", "\r", "\n"])
def test_cmd_refuses_the_characters_that_would_reshape_its_command_line(char):
    """A CR or LF inside a ``/c`` string cuts the line, and the rest runs outside ``/s``."""
    assert encode_workdir(AbsPath(f"C:\\dir{char}x"), ShellDialect.CMD) is None


def test_the_child_starts_in_the_launchers_directory_and_moves_after_the_guard():
    """G24-12 / LAUNCH-09: Windows searches the current directory for DLLs before ``PATH``."""
    spec = policy_spec()
    request = request_for(
        spec, spec.launcher, "Get-Date", "C:\\work", {}, AbsPath("C:\\work"), ()
    )
    assert isinstance(request, PosixLaunch)
    assert request.cwd == AbsPath("C:\\Program Files\\PowerShell\\7")
    assert request.workdir == AbsPath("C:\\work")
    assert "C:\\work" in request.argv[-1]  # the encoded <W> lives in the command line only


def test_the_prelude_guards_before_it_moves_and_carries_the_body_in_one_element():
    """LAUNCH-02 / LAUNCH-05 / LAUNCH-09a: no byte of the body can run ahead of the guard."""
    spec = policy_spec()
    request = request_for(
        spec, spec.launcher, "Get-Date", "C:\\work", {}, AbsPath("C:\\work"), ()
    )
    argument = request.argv[-1]
    assert request.argv[1:4] == ("-NoProfile", "-NonInteractive", "-Command")
    assert argument.index("exit 97") < argument.index("Set-Location")
    assert argument.index("Set-Location") < argument.index("Get-Date")
    assert argument.endswith("; Get-Date")


def test_the_cmd_and_git_bash_lines_use_or_exit_98_rather_than_and():
    r"""LAUNCH-09a: ``&&`` binds tighter than ``;`` and ``&``, so a failed ``cd`` still runs
    the body's second command."""
    oracle = ladder_oracle()
    cmd_spec = attested_spec(
        Rung.cmd, image("C:\\Windows\\System32\\cmd.exe"),
        interpreter("C:\\Windows\\System32\\cmd.exe"), ShellBlock(), oracle,
        Platform.WINDOWS, SUBJECT, True,
    )
    request = request_for(
        cmd_spec, cmd_spec.launcher, "dir", "C:\\work", {}, AbsPath("C:\\work"), ()
    )
    assert isinstance(request, WindowsLaunch)
    assert request.command_line == (
        '"C:\\Windows\\System32\\cmd.exe" /d /e:on /v:off /s /c '
        '"cd /d "C:\\work" || exit 98 & dir"'
    )
    assert "&&" not in request.command_line


def test_the_prelude_refuses_rather_than_dropping_the_session_configuration_check():
    """LAUNCH-06: ``<C>`` may not be quietly omitted, and ``$PSHOME`` cannot stand in for it."""
    assert prelude_for(interpreter(session_config="Restricted"), "C:\\work") is None
    assert prelude_for(interpreter(edition="Co\u2019re"), "C:\\work") is None


# ------------------------------------------------------------------ CFG-02a


@pytest.mark.parametrize(
    "dialect,target,edition,expected",
    [
        (ShellDialect.POWERSHELL, Platform.WINDOWS, "Core", Rung.pwsh),
        (ShellDialect.POWERSHELL, Platform.WINDOWS, "Desktop", Rung.powershell),
        (ShellDialect.POWERSHELL, Platform.WINDOWS, "", None),
        (ShellDialect.POSIX, Platform.WINDOWS, "", Rung.git_bash),
        (ShellDialect.POSIX, Platform.POSIX, "", Rung.system_posix),
        (ShellDialect.CMD, Platform.WINDOWS, "", Rung.cmd),
        (ShellDialect.CMD, Platform.POSIX, "", None),
        (ShellDialect.UNKNOWN, Platform.WINDOWS, "", None),
    ],
)
def test_the_rung_is_derived_from_dialect_platform_and_identity(
    dialect, target, edition, expected
):
    """G14-03 / G14-04: the *target's* platform is read on every row, never the host's."""
    assert derive_rung(dialect, target, interpreter(edition=edition)) is expected


# ------------------------------------------------------------------ SPEC-04a / SPEC-05


@pytest.mark.parametrize("answer,expected", [(True, True), (False, False), (None, False)])
def test_locality_reads_false_when_the_oracle_cannot_answer(answer, expected):
    """G24-20: "cannot answer" is the stricter side, and it must not be an exception.

    A ``-> bool`` signature leaves an unanswerable oracle only one exit, and it would escape
    ``select_rung`` before either policy-off rung had been chosen.
    """
    assert target_is_local(FakeOracle(local=answer)) is expected
    spec = select_rung(ShellBlock(), FakeOracle(local=answer), SUBJECT)
    assert isinstance(spec, ShellSpec) and spec.filesystem_is_local is expected


def test_the_target_platform_is_read_once_and_carried():
    """G18-14: re-asking would let a spec be derived for one platform and measured on another."""
    oracle = FakeOracle()
    select_rung(ShellBlock(), oracle, SUBJECT)
    assert oracle.platform_calls == 1


@pytest.mark.parametrize("method", ORACLE_METHODS)
def test_an_oracle_missing_any_method_leaves_the_rung_unattested(method):
    """G24-11: ``Protocol`` is static; a missing method surfaces as ``AttributeError`` inside
    ``launch()``, long after this call was decided to be allowed."""
    assert oracle_complete(_without(ladder_oracle(), method)) is False


def _without(oracle, method):
    class Missing:
        def __getattr__(self, name):
            if name == method:
                raise AttributeError(name)
            return getattr(oracle, name)

    return Missing()


def test_an_oracle_bound_to_one_subject_refuses_to_answer_about_another():
    """G14-06 / SPEC-05: an answer about some other token attests the wrong process."""
    oracle = ladder_oracle()
    other = Subject("999")
    assert oracle.target_base_env(other) is None
    assert oracle.target_path_entries(other) is None
    assert oracle.target_pinned_env(other) is None
    assert oracle.resolve_image(AbsPath(PWSH), other) is None
    assert oracle.discover(Rung.pwsh, other) is None
    assert oracle.subject_can_replace(AbsPath(PWSH), other) is True


def test_the_environment_inputs_come_from_the_target_and_fail_closed():
    """G24-10 / G24-14: nothing from the floor's own machine reaches the request."""
    oracle = ladder_oracle(base_env={"USERNAME": "target-user"}, project_root=AbsPath("C:\\repo"))
    spec = policy_spec(oracle=oracle)
    got = read_env_inputs(spec, AbsPath("C:\\work"))
    assert isinstance(got, EnvInputs) and got.base["USERNAME"] == "target-user"
    blind = ladder_oracle(project_root=None)
    spec2 = policy_spec(oracle=blind)
    assert isinstance(read_env_inputs(spec2, AbsPath("C:\\work")), Exhausted)


# ------------------------------------------------------------------ LADDER


def test_the_pre_flip_default_is_todays_rung_on_both_platforms():
    """LADDER-05: the ladder runs only after the flip, and until then nothing changes."""
    windows = select_rung(ShellBlock(), FakeOracle(target=Platform.WINDOWS), SUBJECT)
    posix = select_rung(ShellBlock(), FakeOracle(target=Platform.POSIX), SUBJECT)
    assert isinstance(windows, ShellSpec) and windows.rung is Rung.legacy_cmd
    assert isinstance(posix, ShellSpec) and posix.rung is Rung.system_posix
    assert not windows.policy_enabled and not posix.policy_enabled


def test_half_a_shell_block_is_refused_rather_than_half_applied():
    only_path = select_rung(ShellBlock(path=AbsPath(PWSH)), ladder_oracle(), SUBJECT)
    only_dialect = select_rung(
        ShellBlock(dialect=ShellDialect.POWERSHELL), ladder_oracle(), SUBJECT
    )
    assert isinstance(only_path, Exhausted) and isinstance(only_dialect, Exhausted)


def test_an_explicit_source_is_free_of_signature_but_not_of_position():
    """G25-05 / IMG-05 (b): a refused explicit source does not fall back to ``auto``."""
    block = ShellBlock(path=AbsPath(PWSH), dialect=ShellDialect.POWERSHELL)
    writable = ladder_oracle(writable={PWSH})
    assert isinstance(select_rung(block, writable, SUBJECT), Exhausted)


def test_an_explicit_source_inside_the_project_root_is_refused_by_position():
    """G25-06: a read-only checkout's own interpreter passes the chain and fails the position."""
    inside = "C:\\repo\\tools\\pwsh.exe"
    oracle = ladder_oracle(
        identities={inside: interpreter(inside)}, project_root=AbsPath("C:\\repo")
    )
    block = ShellBlock(path=AbsPath(inside), dialect=ShellDialect.POWERSHELL)
    refusal = select_rung(block, oracle, SUBJECT)
    assert isinstance(refusal, Exhausted) and "IMG-05a" in refusal.reason
    blind = ladder_oracle(identities={inside: interpreter(inside)}, project_root=None)
    assert isinstance(select_rung(block, blind, SUBJECT), Exhausted)


def test_an_explicit_posix_source_keeps_the_named_interpreter_on_a_policy_off_rung():
    """G25-07 / CFG-02c: dropping it swaps the user's interpreter for today's, silently."""
    oracle = FakeOracle(
        target=Platform.POSIX,
        identities={"/bin/zsh": interpreter("/bin/zsh", edition="")},
        project_root=AbsPath("/repo"),
    )
    block = ShellBlock(path=AbsPath("/bin/zsh"), dialect=ShellDialect.POSIX)
    spec = select_rung(block, oracle, SUBJECT)
    assert isinstance(spec, ShellSpec)
    assert spec.rung is Rung.system_posix and not spec.policy_enabled
    assert spec.launcher is None and spec.pinned_env is None
    assert spec.explicit_shell == AbsPath("/bin/zsh")
    bash = select_rung(
        ShellBlock(path=AbsPath("/bin/bash"), dialect=ShellDialect.POSIX),
        FakeOracle(
            target=Platform.POSIX,
            identities={"/bin/bash": interpreter("/bin/bash", edition="")},
            project_root=AbsPath("/repo"),
        ),
        SUBJECT,
    )
    assert isinstance(bash, ShellSpec) and bash.fingerprint != spec.fingerprint


def test_two_specs_differing_only_in_one_pin_have_different_fingerprints():
    """G23-11 / IMG-03a: the allowlist in force is frozen onto the spec and fingerprinted."""
    tool = "C:\\Program Files\\Git\\cmd\\git.exe"
    one = policy_spec(allowlist=(HashPin(path=AbsPath(tool), sha256=Sha256("aa")),))
    two = policy_spec(allowlist=(HashPin(path=AbsPath(tool), sha256=Sha256("bb")),))
    assert one.fingerprint != two.fingerprint
    assert one.allowlist != two.allowlist


def test_a_pshome_the_identity_disagrees_with_refuses_at_construction():
    """G21-19: never build a spec validated against A that the re-read checks against B."""
    img = image(PWSH)
    mismatched = interpreter(PWSH, pshome="C:\\Elsewhere", img=img)
    oracle = ladder_oracle(identities={PWSH: mismatched})
    spec = attested_spec(
        Rung.pwsh, img, mismatched, ShellBlock(), oracle, Platform.WINDOWS, SUBJECT, True
    )
    assert isinstance(spec, Exhausted) and "PSHOME" in spec.reason.upper()


def test_a_console_session_configuration_refuses_the_rung():
    """IMG-08: asking the interpreter what its session configuration is runs it first."""
    oracle = ladder_oracle(session="Restricted")
    spec = attested_spec(
        Rung.pwsh, image(PWSH), interpreter(PWSH), ShellBlock(), oracle,
        Platform.WINDOWS, SUBJECT, True,
    )
    assert isinstance(spec, Exhausted) and "session config" in spec.reason


# ------------------------------------------------------------------ NAME-01..03


def cmd_spec(oracle=None) -> ShellSpec:
    spec = attested_spec(
        Rung.cmd, image("C:\\Windows\\System32\\cmd.exe"),
        interpreter("C:\\Windows\\System32\\cmd.exe"), ShellBlock(),
        oracle or ladder_oracle(), Platform.WINDOWS, SUBJECT, True,
    )
    assert isinstance(spec, ShellSpec)
    return spec


def posix_spec(oracle=None) -> ShellSpec:
    path = "C:\\Program Files\\Git\\bin\\bash.exe"
    src = oracle or ladder_oracle(identities={path: interpreter(path, edition="")})
    spec = attested_spec(
        Rung.git_bash, image(path), interpreter(path, edition=""), ShellBlock(), src,
        Platform.WINDOWS, SUBJECT, True,
    )
    assert isinstance(spec, ShellSpec)
    return spec


def test_a_cmd_internal_command_needs_no_image_of_its_own():
    """G04-34 / IMG-02: an in-process entry's image half is the rung's attested launcher.

    A ``PATH`` search for ``dir`` either always fails or gets quietly skipped, and neither is
    an answer about whether ``dir`` may run.
    """
    got = resolve_name("dir", cmd_spec(), ladder_oracle(), ())
    assert got.entry is not None and got.opaque is None


def test_a_bare_word_not_on_the_filtered_path_is_opaque():
    """G07-05: not found is not "harmless", it is unresolvable."""
    spec = cmd_spec()
    got = resolve_name("evil", spec, ladder_oracle(resolvable=set()), ())
    assert got.opaque == "not-found"


def test_a_bare_word_on_the_path_but_absent_from_the_table_is_opaque():
    """G07-06 / IMG-02: an image with no name is a program nobody classified."""
    directory = AbsPath("C:\\Program Files\\Git\\cmd")
    oracle = ladder_oracle(resolvable={"C:\\Program Files\\Git\\cmd\\evil.EXE"})
    got = resolve_name("evil", cmd_spec(), oracle, (directory,))
    assert got.image is not None and got.opaque == "name"


def test_a_trusted_external_program_resolves_on_the_given_search_path():
    """G07-04 / ENV-01a: the search path handed in, never one fetched here."""
    directory = AbsPath("C:\\Program Files\\Git\\cmd")
    oracle = ladder_oracle(resolvable={"C:\\Program Files\\Git\\cmd\\git.EXE"})
    got = resolve_name("git", cmd_spec(), oracle, (directory,))
    assert got.opaque is None and got.entry is not None
    assert got.image.canonical_path == AbsPath("C:\\Program Files\\Git\\cmd\\git.EXE")


def test_the_windows_search_uses_the_pinned_pathext_and_nothing_else():
    """ENV-02: ``.cmd`` and ``.bat`` are off, so a planted ``git.bat`` is not a candidate."""
    directory = AbsPath("C:\\tools")
    oracle = ladder_oracle(resolvable={"C:\\tools\\git.bat"})
    assert resolve("git", cmd_spec(), oracle, (directory,)) is None


def test_bash_searches_the_exact_filename_with_no_extension_rules():
    directory = AbsPath("C:\\Program Files\\Git\\usr\\bin")
    oracle = ladder_oracle(resolvable={"C:\\Program Files\\Git\\usr\\bin\\git"})
    found = resolve("git", posix_spec(), oracle, (directory,))
    assert found is not None


def test_a_word_bash_resolves_before_the_path_search_is_opaque_unless_it_is_inert():
    """NAME-03: bash has already resolved aliases, keywords, functions and builtins away."""
    spec = posix_spec()
    assert resolve_name("eval", spec, ladder_oracle(), ()).opaque is not None
    assert resolve_name("source", spec, ladder_oracle(), ()).opaque is not None
    inert = resolve_name("pwd", spec, ladder_oracle(), ())
    assert inert.opaque is None and inert.entry is not None


def test_every_powershell_bare_word_is_opaque_until_the_table_is_measured():
    """IMG-07 / NAME-02: the rung stands and serves explicit paths; the bare words do not.

    This is deliberately not a reason to refuse the rung. NAME-02 already spells out the same
    degradation for its sibling condition — the closed environment not being established — and
    refusing instead would drop Windows to ``cmd``, whose floor is coarser, over a missing
    *name* table that only a Windows job can measure.
    """
    spec = policy_spec()
    assert spec.rung is Rung.pwsh and spec.policy_enabled
    got = resolve_name("Get-Date", spec, ladder_oracle(), ())
    assert got.opaque == "identity-not-measured"


def measured(monkeypatch, rows) -> None:
    from agentao.permissions_hardline import _trust

    monkeypatch.setitem(
        _trust.MEASURED_COMMAND_TABLES, ("Core", "7.4.6"), tuple(rows)
    )


def test_a_function_shadowing_an_external_program_is_the_entry_that_answers(monkeypatch):
    """G04-35 / NAME-02: function before external program, and it needs its own registration.

    Falling through to whatever it shadowed would classify ``git`` by the trusted table while
    PowerShell runs a function of the same name.
    """
    from agentao.permissions_hardline._trust import MeasuredEntry

    measured(monkeypatch, [
        MeasuredEntry(name="git", kind="function"),
        MeasuredEntry(name="Get-Date", kind="cmdlet"),
    ])
    spec = policy_spec()
    directory = AbsPath("C:\\Program Files\\Git\\cmd")
    oracle = ladder_oracle(resolvable={"C:\\Program Files\\Git\\cmd\\git.EXE"})
    shadowed = resolve_name("git", spec, oracle, (directory,))
    assert shadowed.opaque == "unregistered-function"
    registered = resolve_name("Get-Date", spec, oracle, ())
    assert registered.opaque is None and registered.entry is not None


def test_an_alias_is_judged_by_its_target_image_and_not_by_its_own_name(monkeypatch):
    """G04-36 / IMG-02: a trusted alias name would otherwise launder an untrusted target."""
    from agentao.permissions_hardline._trust import MeasuredEntry

    measured(monkeypatch, [MeasuredEntry(name="g", kind="alias", alias_target="git")])
    spec = policy_spec()
    evil = ladder_oracle(resolvable={"C:\\evil\\git.EXE"}, writable={"C:\\evil"})
    hostile = resolve_name("g", spec, evil, (AbsPath("C:\\evil"),))
    assert hostile.opaque is None  # the name half resolved…
    assert hostile.image.canonical_path == AbsPath("C:\\evil\\git.EXE")  # …to the target's image
    from agentao.permissions_hardline._trust import trusted_image

    assert trusted_image(
        hostile.image, SUBJECT, (), evil, Platform.WINDOWS
    ) is False  # and the image half is what refuses it


def test_a_function_registered_as_inert_is_allowed(monkeypatch):
    """G04-35's other half: a function that shadows a cmdlet is the entry, and may be inert.

    ``mkdir`` is a function under ``-NoProfile``, not a cmdlet, so NAME-02's ordering decides
    the answer for a word people type every day.
    """
    from agentao.permissions_hardline._trust import MeasuredEntry

    measured(monkeypatch, [MeasuredEntry(name="mkdir", kind="function")])
    got = resolve_name("mkdir", policy_spec(), ladder_oracle(), ())
    assert got.opaque is None and got.entry is not None


def test_a_proxy_host_list_is_not_read_as_a_relative_path():
    """ENV-06c: the proxy keys are registered for URLs and host lists.

    ``NO_PROXY=.internal`` is a domain suffix. A leading dot alone does not make a value a
    path, and rejecting it would drop exactly what the key exists to carry.
    """
    spec = policy_spec()
    env = child_env(spec, windows_pinned(), inputs_with({
        "NO_PROXY": ".internal,localhost", "HTTPS_PROXY": "http://proxy:8080",
    }), ())
    assert env["NO_PROXY"] == ".internal,localhost"
    assert env["HTTPS_PROXY"] == "http://proxy:8080"
    bare = child_env(spec, windows_pinned(), inputs_with({"HTTPS_PROXY": "proxy:8080"}), ())
    assert "HTTPS_PROXY" not in bare  # a proxy value still has to be a URL


def test_the_pre_flip_default_is_the_ladder_function_and_not_a_second_copy():
    """One definition of what ``auto`` means before the flip.

    ``default_spec`` and ``select_rung`` were each returning the two policy-off rungs. Two
    homes for one value is the drift this whole design set is organised against, so the
    default delegates: the platform-only oracle answers the two questions rung selection asks
    before either policy-off rung is chosen, and nothing else.
    """
    from agentao.capabilities.shell_spec import default_spec

    for windows, local, rung in (
        (True, True, Rung.legacy_cmd), (False, False, Rung.system_posix)
    ):
        direct = default_spec(windows=windows, local=local)
        through = select_rung(
            ShellBlock(),
            FakeOracle(target=Platform.WINDOWS if windows else Platform.POSIX, local=local),
            direct.execution_subject,
        )
        assert isinstance(through, ShellSpec)
        assert direct.rung is rung and through.rung is rung
        assert direct.fingerprint == through.fingerprint


def test_the_preflight_runs_a_prelude_that_can_actually_change_directory():
    """IMG-09 uses the *same* prelude, so it needs a working directory of its own.

    An empty ``<W>`` makes the prelude's ``Set-Location`` fail and the child exit 98, which
    reads back as "the closed environment was not established" for every healthy interpreter —
    a silent degradation that no fake oracle returning ``True`` would ever show.
    """
    seen = {}

    class RecordingOracle(FakeOracle):
        def preflight(self, identity, prelude):
            seen["prelude"] = prelude
            return True

    spec = attested_spec(
        Rung.pwsh, image(PWSH), interpreter(PWSH), ShellBlock(),
        RecordingOracle(
            discovered={Rung.pwsh: image(PWSH)},
            identities={PWSH: interpreter(PWSH)},
            trusted_publishers={PWSH},
            pshome=AbsPath("C:\\Program Files\\PowerShell\\7"),
        ),
        Platform.WINDOWS, SUBJECT, True,
    )
    assert isinstance(spec, ShellSpec) and spec.closed_env_established is True
    assert "Set-Location -LiteralPath 'C:\\Program Files\\PowerShell\\7'" in seen["prelude"]
