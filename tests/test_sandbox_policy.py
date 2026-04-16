"""Tests for the macOS sandbox-exec integration."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agentao.sandbox import (
    SandboxMisconfiguredError,
    SandboxPolicy,
    SandboxProfile,
    load_sandbox_config,
)
from agentao.tools.shell import _wrap_with_sandbox, _annotate_sandbox_denial


MACOS_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS only")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_default_config_is_disabled():
    """When no config file exists, defaults are safe (disabled, darwin)."""
    with patch("agentao.sandbox.policy._load_json", return_value=({}, None)):
        cfg = load_sandbox_config(project_root=Path("/nonexistent"))
    assert cfg["enabled"] is False
    assert cfg["platform"] == "darwin"
    assert cfg["default_profile"] == "workspace-write-no-network"
    assert "_load_errors" not in cfg


def test_project_config_overrides_home(tmp_path):
    """Project-level config wins over home-level on matching keys."""
    home_cfg = {"enabled": True, "default_profile": "readonly"}
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / ".agentao").mkdir()
    (proj_dir / ".agentao" / "sandbox.json").write_text(
        json.dumps({"default_profile": "workspace-write"})
    )

    def _fake_load(path: Path):
        if str(path).startswith(str(Path.home())):
            return home_cfg, None
        if path == proj_dir / ".agentao" / "sandbox.json":
            return json.loads(path.read_text()), None
        return {}, None

    with patch("agentao.sandbox.policy._load_json", side_effect=_fake_load):
        cfg = load_sandbox_config(project_root=proj_dir)

    assert cfg["enabled"] is True  # from home
    assert cfg["default_profile"] == "workspace-write"  # project wins


def test_project_null_clears_inherited_home_value():
    """Regression: a project file with an explicit `null` must be able to
    reset an inherited user-level setting (profiles_dir / workspace_root)
    back to the documented default of None. Previously the merge loop
    silently dropped null values and let user-global paths leak into
    every project."""
    home_cfg = {"enabled": True, "profiles_dir": "/my/user/profiles"}
    project_cfg = {"profiles_dir": None}

    def _fake_load(path: Path):
        if str(path).startswith(str(Path.home())):
            return home_cfg, None
        return project_cfg, None

    with patch("agentao.sandbox.policy._load_json", side_effect=_fake_load):
        cfg = load_sandbox_config(project_root=Path("/tmp/irrelevant"))

    # Explicit project null beats inherited user path
    assert cfg["profiles_dir"] is None
    # Other inherited values still survive
    assert cfg["enabled"] is True


def test_malformed_sandbox_json_fails_closed(tmp_path):
    """Regression: if sandbox.json exists on disk but cannot be parsed,
    the policy must fail-closed (resolve raises) instead of silently
    falling back to the default-disabled config — that would execute
    shell commands completely unsandboxed despite user intent to the
    contrary.
    """
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / ".agentao").mkdir()
    (proj_dir / ".agentao" / "sandbox.json").write_text("{ not valid json")

    # Need to patch HOME so the test doesn't accidentally pick up a real
    # user-level sandbox.json sitting outside the fixture.
    with patch.object(Path, "home", return_value=tmp_path / "home"):
        cfg = load_sandbox_config(project_root=proj_dir)
        policy = SandboxPolicy(project_root=proj_dir, config=cfg)

    # Parse error surfaced through health_error + enabled (on macOS)
    err = policy.health_error()
    assert err is not None
    assert "invalid JSON" in err
    if sys.platform == "darwin":
        assert policy.enabled, "must be fail-closed-enabled to block shell execution"
        with pytest.raises(SandboxMisconfiguredError) as exc:
            policy.resolve("run_shell_command", {"command": "ls"})
        assert "could not be parsed" in str(exc.value)


def test_malformed_platform_field_does_not_silently_disable_sandbox(tmp_path):
    """Regression: `"platform": 123` (wrong JSON type) records a validation
    error in `_load_errors`, but previously the `enabled` getter checked
    `_is_platform_supported(123)` *before* the error-check branch. On macOS
    that made `.enabled` return False and `run_shell_command` run
    unsandboxed — the exact fail-open case the validation was meant to
    block. Must fail-closed instead."""
    proj_dir = tmp_path / "proj"
    (proj_dir / ".agentao").mkdir(parents=True)
    (proj_dir / ".agentao" / "sandbox.json").write_text(
        '{"enabled": true, "platform": 123, "default_profile": "workspace-write-no-network"}'
    )

    with patch.object(Path, "home", return_value=tmp_path / "home"):
        cfg = load_sandbox_config(project_root=proj_dir)
        policy = SandboxPolicy(project_root=proj_dir, config=cfg)

    err = policy.health_error()
    assert err is not None
    assert "platform" in err

    if sys.platform == "darwin":
        assert policy.enabled, "malformed platform must fail-closed-enabled on macOS"
        with pytest.raises(SandboxMisconfiguredError):
            policy.resolve("run_shell_command", {"command": "ls"})

        policy.set_enabled(False)
        assert not policy.enabled
        assert policy.resolve("run_shell_command", {"command": "ls"}) is None


def test_session_off_overrides_malformed_config_fail_closed(tmp_path):
    """Regression: the documented `/sandbox off` escape hatch must work
    even when disk config is broken. Without the override, a JSON typo
    would lock every shell command for the whole session with no
    in-process recovery — that's far worse than unsandboxed execution
    because the user consciously turned sandboxing off."""
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / ".agentao").mkdir()
    (proj_dir / ".agentao" / "sandbox.json").write_text("{ not valid json")

    with patch.object(Path, "home", return_value=tmp_path / "home"):
        cfg = load_sandbox_config(project_root=proj_dir)
        policy = SandboxPolicy(project_root=proj_dir, config=cfg)

    # Default (no override): fail-closed on macOS
    if sys.platform == "darwin":
        assert policy.enabled
        with pytest.raises(SandboxMisconfiguredError):
            policy.resolve("run_shell_command", {"command": "ls"})

    # `/sandbox off` must disable regardless of disk state
    policy.set_enabled(False)
    assert not policy.enabled
    assert policy.resolve("run_shell_command", {"command": "ls"}) is None

    # `/sandbox on` re-engages fail-closed (same as before)
    policy.set_enabled(True)
    if sys.platform == "darwin":
        assert policy.enabled
        with pytest.raises(SandboxMisconfiguredError):
            policy.resolve("run_shell_command", {"command": "ls"})


def test_wrong_typed_config_fields_do_not_crash(tmp_path):
    """Regression: `{"profiles_dir": 123}` is syntactically valid JSON but
    the old code would TypeError inside Path() during /sandbox status or
    /sandbox profile. The policy must defend against non-string path
    fields (both load-time validation AND runtime tolerance)."""
    # Load-time: validation produces a health error, policy fail-closes
    proj = tmp_path / "proj"
    (proj / ".agentao").mkdir(parents=True)
    (proj / ".agentao" / "sandbox.json").write_text(
        '{"enabled": true, "profiles_dir": 123}'
    )
    with patch.object(Path, "home", return_value=tmp_path / "home"):
        cfg = load_sandbox_config(project_root=proj)
        policy = SandboxPolicy(project_root=proj, config=cfg)

    err = policy.health_error()
    assert err is not None
    assert "profiles_dir" in err
    # list_profiles() must NOT crash despite the bad type
    names = policy.list_profiles()
    assert "workspace-write-no-network" in names  # built-ins still visible
    # And the `/sandbox off` escape hatch still works
    policy.set_enabled(False)
    assert not policy.enabled


def test_policy_tolerates_non_string_path_fields_injected_directly():
    """Belt-and-suspenders: even when a test or external caller skips
    load-time validation and hands a bad-typed config to SandboxPolicy
    directly, path helpers must not raise."""
    policy = SandboxPolicy(config={
        "enabled": True,
        "platform": "darwin",
        "profiles_dir": 42,        # wrong type
        "workspace_root": ["not", "a", "string"],  # also wrong
        "default_profile": "workspace-write-no-network",
    })
    # Both should fall back to safe defaults rather than crashing
    _ = policy.workspace_root            # project root fallback
    _ = policy.list_profiles()           # built-ins only
    _ = policy._locate_profile("workspace-write-no-network")  # still found


def test_non_object_sandbox_json_fails_closed(tmp_path):
    """Array or scalar at the root is also a config error, not silently ignored."""
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / ".agentao").mkdir()
    (proj_dir / ".agentao" / "sandbox.json").write_text("[1, 2, 3]")

    with patch.object(Path, "home", return_value=tmp_path / "home"):
        cfg = load_sandbox_config(project_root=proj_dir)
        policy = SandboxPolicy(project_root=proj_dir, config=cfg)

    err = policy.health_error()
    assert err is not None
    assert "root must be a JSON object" in err


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------


def test_resolve_returns_none_when_disabled():
    p = SandboxPolicy(config={"enabled": False, "platform": "darwin"})
    assert p.resolve("run_shell_command", {"command": "ls"}) is None


def test_resolve_returns_none_when_platform_unsupported():
    """Non-darwin always disables, even if `enabled: true` in config."""
    p = SandboxPolicy(config={"enabled": True, "platform": "linux"})
    assert p.resolve("run_shell_command", {"command": "ls"}) is None


def test_resolve_skips_non_shell_tools():
    p = SandboxPolicy(config={
        "enabled": True,
        "platform": "darwin",
        "default_profile": "workspace-write-no-network",
    })
    # Even when enabled, only run_shell_command gets wrapped
    assert p.resolve("write_file", {"file_path": "/x"}) is None
    assert p.resolve("read_file", {"file_path": "/x"}) is None


@MACOS_ONLY
def test_resolve_returns_profile_for_shell_when_enabled(tmp_path):
    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "default_profile": "workspace-write-no-network",
        },
    )
    profile = p.resolve("run_shell_command", {"command": "ls"})
    assert profile is not None
    assert profile.name == "workspace-write-no-network"
    assert profile.path.is_file()
    assert profile.params["_RW1"] == str(tmp_path.resolve())


@MACOS_ONLY
def test_per_tool_rule_overrides_default(tmp_path):
    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "default_profile": "workspace-write-no-network",
            "rules": [{"tool": "run_shell_command", "profile": "readonly"}],
        },
    )
    profile = p.resolve("run_shell_command", {"command": "ls"})
    assert profile is not None
    assert profile.name == "readonly"


@MACOS_ONLY
def test_unknown_profile_name_raises_instead_of_silently_disabling(tmp_path):
    """Regression: when enabled=True but no profile resolves, resolve()
    must raise (fail-closed) rather than return None (fail-open). Returning
    None would make ToolRunner skip the wrap and run the shell unsandboxed,
    silently breaking the protection the user asked for."""
    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "default_profile": "does-not-exist",
        },
    )
    with pytest.raises(SandboxMisconfiguredError) as exc:
        p.resolve("run_shell_command", {"command": "ls"})
    assert "does-not-exist" in str(exc.value)
    assert "/sandbox off" in str(exc.value)


def test_health_error_reports_broken_config(tmp_path):
    """health_error() exposes WHY the sandbox cannot function so the CLI can
    warn the user without having to wait for a tool call to fail."""
    # Disabled → no error reported
    p = SandboxPolicy(config={"enabled": False, "platform": "darwin"})
    assert p.health_error() is None

    # Enabled with bogus profile → specific error. On non-macOS,
    # `health_error()` short-circuits at the platform check before ever
    # reaching profile resolution, so the "typoed" assertion only holds
    # on macOS.
    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "default_profile": "typoed",
        },
    )
    err = p.health_error()
    assert err is not None
    if sys.platform == "darwin":
        assert "typoed" in err
    else:
        assert "macOS" in err

    # Enabled with valid profile → no error (macOS) / platform error (other)
    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "default_profile": "workspace-write-no-network",
        },
    )
    if sys.platform == "darwin":
        assert p.health_error() is None


@MACOS_ONLY
def test_health_error_catches_malformed_profile_syntax(tmp_path):
    """Regression: health_error() must preflight the profile through
    sandbox-exec, not just check `is_file()`. Previously a custom profile
    with invalid TinyScheme syntax would make /sandbox status report
    'healthy' while every run_shell_command subsequently died with
    'Invalid sandbox profile'. The CLI is the early-warning path — it
    has to actually try to load the profile."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    # Syntactically garbage — sandbox-exec will refuse to compile it.
    (profiles / "broken.sb").write_text("(this is not scheme ))")

    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "profiles_dir": str(profiles),
            "default_profile": "broken",
        },
    )

    err = p.health_error()
    assert err is not None, "malformed profile must surface from health_error"
    assert "broken" in err
    assert "failed to load" in err


@MACOS_ONLY
def test_profile_health_error_rejects_malformed_candidate(tmp_path):
    """Regression: `/sandbox profile <name>` uses this to refuse a switch
    to a profile whose file exists but cannot be compiled by sandbox-exec.
    Previously the CLI only ran `_locate_profile()`, so it happily
    confirmed a switch to a broken profile and every subsequent shell
    call blew up with 'Invalid sandbox profile'."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "valid.sb").write_text("(version 1)(allow default)")
    (profiles / "broken.sb").write_text("(this is not scheme ))")

    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": False,  # switch can happen even while sandbox off
            "platform": "darwin",
            "profiles_dir": str(profiles),
        },
    )

    # Non-existent profile → not-found error
    err = p.profile_health_error("nope")
    assert err is not None and "not found" in err

    # Malformed profile → compile error from sandbox-exec
    err = p.profile_health_error("broken")
    assert err is not None
    assert "failed to load" in err
    assert "broken" in err

    # Valid profile → clean
    assert p.profile_health_error("valid") is None


@MACOS_ONLY
def test_health_error_passes_for_valid_custom_profile(tmp_path):
    """Counterpart: a syntactically valid custom profile should preflight
    cleanly so the CLI doesn't falsely warn."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "custom.sb").write_text("(version 1)(allow default)")

    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "profiles_dir": str(profiles),
            "default_profile": "custom",
        },
    )
    assert p.health_error() is None


def test_rule_profile_for_detects_shadowing(tmp_path):
    """rule_profile_for() returns the profile name that a per-tool rule
    would select, so the CLI can warn that `/sandbox profile X` is
    ineffective when a rule already binds run_shell_command to Y."""
    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "default_profile": "workspace-write-no-network",
            "rules": [{"tool": "run_shell_command", "profile": "readonly"}],
        },
    )
    assert p.rule_profile_for("run_shell_command") == "readonly"
    assert p.rule_profile_for("write_file") is None

    # No rules at all → None
    p2 = SandboxPolicy(config={"enabled": True, "platform": "darwin"})
    assert p2.rule_profile_for("run_shell_command") is None


def test_session_toggles():
    p = SandboxPolicy(config={"enabled": False, "platform": "darwin"})
    assert not p.enabled
    p.set_enabled(True)
    # only actually enabled when platform matches
    if sys.platform == "darwin":
        assert p.enabled
    p.set_default_profile("readonly")
    assert p.default_profile_name == "readonly"


def test_list_profiles_includes_all_builtins():
    p = SandboxPolicy(config={"enabled": False})
    names = p.list_profiles()
    assert "readonly" in names
    assert "workspace-write" in names
    assert "workspace-write-no-network" in names


# ---------------------------------------------------------------------------
# Command wrapping
# ---------------------------------------------------------------------------


@MACOS_ONLY
def test_wrap_with_sandbox_produces_correct_shell_string(tmp_path):
    p = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "default_profile": "workspace-write-no-network",
        },
    )
    profile = p.resolve("run_shell_command", {"command": "ls"})
    assert profile is not None

    wrapped = _wrap_with_sandbox("echo hello && ls -la", profile)

    assert wrapped.startswith("sandbox-exec")
    assert f"-D _RW1={tmp_path.resolve()}" in wrapped
    assert str(profile.path) in wrapped
    assert "/bin/sh -c" in wrapped
    # The original command must be shell-quoted so && doesn't leak out.
    assert "'echo hello && ls -la'" in wrapped


def test_wrap_with_sandbox_is_noop_on_non_macos(tmp_path):
    """If somehow called on non-macOS, wrapping returns the command unchanged."""
    profile = SandboxProfile(
        name="test",
        path=tmp_path / "p.sb",
        workspace_root=tmp_path,
        params={"_RW1": str(tmp_path)},
    )
    with patch("agentao.tools.shell.IS_MACOS", False):
        assert _wrap_with_sandbox("ls", profile) == "ls"


@MACOS_ONLY
def test_wrap_with_sandbox_handles_single_quotes(tmp_path):
    """shlex.quote must handle commands containing single quotes safely.

    Gated to macOS because `_wrap_with_sandbox` is a no-op on other
    platforms (sandbox-exec does not exist there) — see the adjacent
    `test_wrap_with_sandbox_is_noop_on_non_macos`."""
    profile = SandboxProfile(
        name="readonly",
        path=Path(__file__).parent.parent / "agentao" / "sandbox" / "profiles" / "readonly.sb",
        workspace_root=tmp_path,
        params={"_RW1": str(tmp_path)},
    )
    # sh's own quoting: 'it'\''s' is the POSIX-portable way to embed a '
    wrapped = _wrap_with_sandbox("echo 'it's'", profile)
    # Whatever shlex does, it must not leave the outer quoting ambiguous
    assert wrapped.count("/bin/sh -c") == 1


# ---------------------------------------------------------------------------
# Denial annotation
# ---------------------------------------------------------------------------


def test_annotate_adds_hint_on_denial_marker(tmp_path):
    profile = SandboxProfile(
        name="workspace-write-no-network",
        path=tmp_path / "p.sb",
        workspace_root=tmp_path,
        params={"_RW1": str(tmp_path)},
    )
    result = "STDERR: echo: /etc/foo: Operation not permitted\n\nExit code: 1"
    annotated = _annotate_sandbox_denial(result, profile)
    assert "Sandbox hint" in annotated
    assert "workspace-write-no-network" in annotated
    assert "`/sandbox off`" in annotated


def test_annotate_is_noop_on_success(tmp_path):
    profile = SandboxProfile(
        name="workspace-write-no-network",
        path=tmp_path / "p.sb",
        workspace_root=tmp_path,
        params={"_RW1": str(tmp_path)},
    )
    result = "STDOUT:\nhello\n"
    assert _annotate_sandbox_denial(result, profile) == result


def test_annotate_ignores_wrapped_command_echo_in_background_result(tmp_path):
    """Regression: the background-start path echoes the wrapped command
    back to the caller; the word 'sandbox-exec' in that echo must not be
    treated as a denial signal."""
    profile = SandboxProfile(
        name="workspace-write-no-network",
        path=tmp_path / "p.sb",
        workspace_root=tmp_path,
        params={"_RW1": str(tmp_path)},
    )
    result = (
        "Background process started.\n"
        "PID: 12345\n"
        "PGID: 12345\n"
        f"Command: sandbox-exec -D _RW1={tmp_path} -f {tmp_path}/p.sb /bin/sh -c 'server'\n"
        f"Working directory: {tmp_path}\n"
        "To stop: kill -- -12345"
    )
    assert _annotate_sandbox_denial(result, profile) == result


def test_annotate_ignores_wrapped_command_echo_in_timeout_result(tmp_path):
    """Regression: the inactivity-timeout path also echoes the wrapped
    command. It must not trigger the denial hint."""
    profile = SandboxProfile(
        name="workspace-write-no-network",
        path=tmp_path / "p.sb",
        workspace_root=tmp_path,
        params={"_RW1": str(tmp_path)},
    )
    result = (
        "Command timed out after 120s of inactivity.\n"
        f"Command: sandbox-exec -D _RW1={tmp_path} -f {tmp_path}/p.sb /bin/sh -c 'sleep 999'"
    )
    assert _annotate_sandbox_denial(result, profile) == result


# ---------------------------------------------------------------------------
# Regression: path resolution anchored to project, not process cwd
# ---------------------------------------------------------------------------


def test_relative_workspace_root_resolves_against_project_not_cwd(tmp_path, monkeypatch):
    """Regression: a project-level `sandbox.json` with a relative
    `workspace_root` must anchor to the owning project, not whichever
    directory the host process happens to be in."""
    proj = tmp_path / "proj"
    (proj / "subtree").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()

    # Process cwd is deliberately NOT the project
    monkeypatch.chdir(other)

    p = SandboxPolicy(
        project_root=proj,
        config={
            "enabled": True,
            "platform": "darwin",
            "workspace_root": "subtree",   # relative!
            "default_profile": "workspace-write-no-network",
        },
    )
    assert p.workspace_root == (proj / "subtree").resolve()
    assert p.workspace_root != (other / "subtree").resolve()


def test_relative_profiles_dir_resolves_against_project_not_cwd(tmp_path, monkeypatch):
    """Regression: relative `profiles_dir` must anchor to the project."""
    proj = tmp_path / "proj"
    profiles = proj / ".agentao" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "custom.sb").write_text("(version 1)(allow default)")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)

    p = SandboxPolicy(
        project_root=proj,
        config={
            "enabled": True,
            "platform": "darwin",
            "profiles_dir": ".agentao/profiles",  # relative!
            "default_profile": "custom",
        },
    )
    located = p._locate_profile("custom")
    assert located is not None
    assert located == (profiles / "custom.sb").resolve()
    assert "custom" in p.list_profiles()


def test_home_relative_profiles_dir_anchors_at_home_not_project(tmp_path):
    """Regression: a relative `profiles_dir` in ~/.agentao/sandbox.json must
    resolve under ~/.agentao/, NOT under whatever project later consumes the
    merged config. Previously the merge step stripped provenance and then
    `_resolve_config_path` rebased every relative path onto the project
    root — so a shared user-level `"profiles_dir": "profiles"` became
    `<project>/profiles` and the user's profile file was invisible.
    """
    fake_home = tmp_path / "home"
    (fake_home / ".agentao" / "profiles").mkdir(parents=True)
    (fake_home / ".agentao" / "profiles" / "homemade.sb").write_text(
        "(version 1)(allow default)"
    )
    (fake_home / ".agentao" / "sandbox.json").write_text(
        json.dumps({
            "enabled": True,
            "default_profile": "homemade",
            "profiles_dir": "profiles",  # home-relative!
        })
    )

    proj = tmp_path / "proj"
    proj.mkdir()

    with patch.object(Path, "home", return_value=fake_home):
        cfg = load_sandbox_config(project_root=proj)
        policy = SandboxPolicy(project_root=proj, config=cfg)

    # The home-relative path must have been baked to ~/.agentao/profiles
    # during load, not rebased onto <project>/profiles at runtime.
    assert cfg["profiles_dir"] == str(fake_home / ".agentao" / "profiles")
    located = policy._locate_profile("homemade")
    assert located == (fake_home / ".agentao" / "profiles" / "homemade.sb").resolve()
    assert "homemade" in policy.list_profiles()


def test_home_relative_workspace_root_anchors_at_home_not_project(tmp_path):
    """Same provenance rule for `workspace_root`: a home-level relative path
    must resolve under ~/.agentao/, not under the project root."""
    fake_home = tmp_path / "home"
    (fake_home / ".agentao" / "ws").mkdir(parents=True)
    (fake_home / ".agentao" / "sandbox.json").write_text(
        json.dumps({"enabled": True, "workspace_root": "ws"})
    )
    proj = tmp_path / "proj"
    proj.mkdir()

    with patch.object(Path, "home", return_value=fake_home):
        cfg = load_sandbox_config(project_root=proj)
        policy = SandboxPolicy(project_root=proj, config=cfg)

    assert cfg["workspace_root"] == str(fake_home / ".agentao" / "ws")
    assert policy.workspace_root == (fake_home / ".agentao" / "ws").resolve()


def test_absolute_workspace_root_is_honoured_as_is(tmp_path):
    abs_path = tmp_path / "abs_workspace"
    abs_path.mkdir()
    p = SandboxPolicy(
        project_root=tmp_path / "irrelevant",
        config={
            "enabled": True,
            "platform": "darwin",
            "workspace_root": str(abs_path),
        },
    )
    assert p.workspace_root == abs_path.resolve()


# ---------------------------------------------------------------------------
# Regression: sandbox policy must track live working directory
# ---------------------------------------------------------------------------


def test_project_root_provider_follows_live_cwd(tmp_path, monkeypatch):
    """Regression: when given a provider (instead of a fixed root), the
    policy must re-read config on cwd drift so ACP/embedded flows that
    chdir between turns pick up the new project's sandbox.json instead of
    the old one."""
    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "proj_b"
    (proj_a / ".agentao").mkdir(parents=True)
    (proj_b / ".agentao").mkdir(parents=True)
    (proj_a / ".agentao" / "sandbox.json").write_text(
        '{"enabled": true, "default_profile": "readonly"}'
    )
    (proj_b / ".agentao" / "sandbox.json").write_text(
        '{"enabled": true, "default_profile": "workspace-write"}'
    )

    with patch.object(Path, "home", return_value=tmp_path / "home"):
        p = SandboxPolicy(project_root_provider=Path.cwd)

        monkeypatch.chdir(proj_a)
        assert p.default_profile_name == "readonly"
        assert p.workspace_root == proj_a.resolve()

        monkeypatch.chdir(proj_b)
        assert p.default_profile_name == "workspace-write"
        assert p.workspace_root == proj_b.resolve()


def test_session_overrides_survive_cwd_drift(tmp_path, monkeypatch):
    """Regression: `/sandbox on` / `/sandbox profile X` should persist
    across a chdir — they are session state, not project state.

    Disk-loaded fields (e.g. rules from sandbox.json) follow the new cwd,
    but explicit session toggles stay put."""
    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "proj_b"
    (proj_a / ".agentao").mkdir(parents=True)
    (proj_b / ".agentao").mkdir(parents=True)
    (proj_a / ".agentao" / "sandbox.json").write_text(
        '{"enabled": false, "default_profile": "readonly"}'
    )
    (proj_b / ".agentao" / "sandbox.json").write_text(
        '{"enabled": false, "default_profile": "workspace-write"}'
    )

    with patch.object(Path, "home", return_value=tmp_path / "home"):
        p = SandboxPolicy(project_root_provider=Path.cwd)
        monkeypatch.chdir(proj_a)
        p.set_enabled(True)
        p.set_default_profile("workspace-write-no-network")

        assert p.default_profile_name == "workspace-write-no-network"

        monkeypatch.chdir(proj_b)
        # Session override beats whatever proj_b's disk config said.
        assert p.default_profile_name == "workspace-write-no-network"
        if sys.platform == "darwin":
            assert p.enabled


def test_project_root_and_provider_are_mutually_exclusive():
    with pytest.raises(ValueError):
        SandboxPolicy(
            project_root=Path("/tmp"),
            project_root_provider=Path.cwd,
        )


# ---------------------------------------------------------------------------
# Regression: sandbox injection must not poison plugin-hook payloads
# ---------------------------------------------------------------------------


@MACOS_ONLY
def test_sandbox_injection_does_not_mutate_hook_visible_args(tmp_path):
    """Regression: ToolRunner passes `_args` to Pre/Post-tool plugin hooks
    which JSON-serialize it. The sandbox profile must be threaded through
    to `.execute()` WITHOUT mutating the shared `_args` dict."""
    import json

    policy = SandboxPolicy(
        project_root=tmp_path,
        config={
            "enabled": True,
            "platform": "darwin",
            "default_profile": "workspace-write-no-network",
        },
    )

    # This mirrors the exact pattern in ToolRunner._execute_one after the fix.
    original_args = {"command": "ls"}
    call_args = original_args
    profile = policy.resolve("run_shell_command", original_args)
    if profile is not None:
        call_args = {**original_args, "_sandbox_profile": profile}

    # 1. The original dict (the one hooks see) is unchanged.
    assert "_sandbox_profile" not in original_args
    assert call_args is not original_args

    # 2. The original dict remains JSON-serializable — if the SandboxProfile
    #    had leaked in, json.dumps would raise TypeError on the Path field.
    json.dumps(original_args)

    # 3. The call-time dict does carry the profile (for ShellTool to use).
    assert call_args["_sandbox_profile"] is profile


# ---------------------------------------------------------------------------
# macOS-only integration: profiles parse and actually sandbox
# ---------------------------------------------------------------------------


@MACOS_ONLY
def test_all_builtin_profiles_pass_sandbox_exec_parser():
    """Every built-in .sb file must be accepted by the kernel's TinyScheme parser."""
    import subprocess
    profiles_dir = Path(__file__).parent.parent / "agentao" / "sandbox" / "profiles"
    assert profiles_dir.is_dir()
    profiles = list(profiles_dir.glob("*.sb"))
    assert profiles, "expected built-in profiles to exist"

    # Use `/bin/sh -c :` because `/bin/true` is not always present on macOS
    # (e.g. it lives at /usr/bin/true on Sequoia+). The `:` builtin is a
    # no-op that does not require extra file-read permissions.
    for sb in profiles:
        r = subprocess.run(
            ["sandbox-exec", "-D", "_RW1=/tmp", "-f", str(sb), "/bin/sh", "-c", ":"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, f"profile {sb.name} failed to parse: {r.stderr}"


@MACOS_ONLY
def test_no_network_profile_actually_blocks_curl(tmp_path):
    """Integration: curl should fail under workspace-write-no-network."""
    import subprocess
    sb = Path(__file__).parent.parent / "agentao" / "sandbox" / "profiles" / "workspace-write-no-network.sb"
    r = subprocess.run(
        [
            "sandbox-exec", "-D", f"_RW1={tmp_path}", "-f", str(sb),
            "/bin/sh", "-c", "curl -sS --max-time 3 https://example.com -o /dev/null",
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode != 0, "curl unexpectedly succeeded under no-network profile"


@MACOS_ONLY
def test_workspace_write_profile_blocks_writes_outside(tmp_path):
    """Integration: writing outside _RW1 must be denied; inside must succeed."""
    import subprocess
    sb = Path(__file__).parent.parent / "agentao" / "sandbox" / "profiles" / "workspace-write-no-network.sb"

    # Inside the workspace — should succeed
    inside = subprocess.run(
        [
            "sandbox-exec", "-D", f"_RW1={tmp_path}", "-f", str(sb),
            "/bin/sh", "-c", f"echo ok > {tmp_path}/inside.txt && cat {tmp_path}/inside.txt",
        ],
        capture_output=True, text=True, timeout=10,
    )
    assert inside.returncode == 0, f"write inside workspace failed: {inside.stderr}"
    assert "ok" in inside.stdout

    # Outside the workspace — should be denied. We target a path we know
    # exists (/private/etc is protected by both sandbox and SIP, so EPERM
    # is guaranteed) and don't actually create anything persistent.
    outside = subprocess.run(
        [
            "sandbox-exec", "-D", f"_RW1={tmp_path}", "-f", str(sb),
            "/bin/sh", "-c", "echo bad > /private/etc/agentao_sandbox_probe",
        ],
        capture_output=True, text=True, timeout=10,
    )
    assert outside.returncode != 0, "write to /private/etc unexpectedly succeeded"
    assert "Operation not permitted" in (outside.stdout + outside.stderr)
