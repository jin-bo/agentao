"""Sandbox policy: decides whether to wrap a tool call in sandbox-exec.

On macOS, when enabled, `run_shell_command` is wrapped in a sandbox-exec
subprocess that restricts filesystem writes (and optionally network) based
on a named profile. The policy is a strict *added* layer — it runs after
the permission engine already decided ALLOW.

Config precedence (lower overrides higher? No — project overrides home):
    1. ~/.agentao/sandbox.json           (user-level defaults)
    2. <project>/.agentao/sandbox.json    (project overrides user)

Schema::

    {
      "enabled": false,
      "platform": "darwin",
      "default_profile": "workspace-write-no-network",
      "rules": [
        {"tool": "run_shell_command", "profile": "workspace-write"}
      ],
      "profiles_dir": null,
      "workspace_root": null
    }

`profiles_dir` (if set) is searched *before* the built-in profile dir so
users can override built-ins by dropping a file with the same name.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


_BUILTIN_PROFILES_DIR = Path(__file__).parent / "profiles"


class SandboxMisconfiguredError(RuntimeError):
    """Raised when sandbox is enabled but no profile can be resolved.

    Signals a fail-closed condition: the user asked for sandboxing, but the
    config references a profile name that does not exist on disk. Callers
    (ToolRunner) must treat this as a tool-call failure rather than falling
    back to unsandboxed execution — that would silently strip the protection
    the user believed was active.
    """

_DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "platform": "darwin",
    "default_profile": "workspace-write-no-network",
    "rules": [],
    "profiles_dir": None,
    "workspace_root": None,
}


@dataclass(frozen=True)
class SandboxProfile:
    """Resolved sandbox profile ready to be applied to a subprocess."""
    name: str
    path: Path
    workspace_root: Path
    params: Dict[str, str] = field(default_factory=dict)

    def as_args(self) -> List[str]:
        """Return argv *prefix* for sandbox-exec (without the wrapped command).

        Example output::

            ["sandbox-exec", "-D", "_RW1=/Users/me/proj", "-f", "/abs/p.sb"]
        """
        out: List[str] = ["sandbox-exec"]
        for k, v in self.params.items():
            out.extend(["-D", f"{k}={v}"])
        out.extend(["-f", str(self.path)])
        return out


def _load_json(path: Path) -> Tuple[Dict[str, Any], Optional[str]]:
    """Load a sandbox config file.

    Returns `(data, error)`:
      - `data` is the parsed dict (or `{}` if the file is missing)
      - `error` is non-None **only** when the file exists on disk but
        could not be parsed / read / coerced to an object. The caller must
        treat a non-None error as a fail-closed signal: a present-but-
        broken config means we cannot tell whether the user wanted
        sandboxing, and silently falling back to the default-disabled
        config would hide a real protection failure.
    """
    if not path.is_file():
        return {}, None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return {}, f"{path}: unreadable ({e})"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {}, f"{path}: invalid JSON (line {e.lineno}, col {e.colno}: {e.msg})"
    if not isinstance(data, dict):
        return {}, f"{path}: root must be a JSON object, got {type(data).__name__}"
    return data, None


# Sentinel key used to smuggle parse errors through the returned config so
# SandboxPolicy can fail-closed. NOT a documented public config field.
_PARSE_ERRORS_KEY = "_load_errors"


# Expected Python type(s) for each user-writable top-level config field.
# `None` is allowed wherever the documented default is null.
_FIELD_TYPES: Dict[str, tuple] = {
    "enabled": (bool,),
    "platform": (str,),
    "default_profile": (str, type(None)),
    "rules": (list,),
    "profiles_dir": (str, type(None)),
    "workspace_root": (str, type(None)),
}


_PATH_FIELDS: Tuple[str, ...] = ("profiles_dir", "workspace_root")


def _absolutize_path_fields(cfg: Dict[str, Any], anchor: Path) -> None:
    """Rewrite relative path fields in-place so they carry their origin.

    A home-level config like ``~/.agentao/sandbox.json`` with
    ``"profiles_dir": "profiles"`` means *this user's* profiles dir — i.e.
    ``~/.agentao/profiles`` — not "some dir called profiles under whatever
    project the agent happens to load next". Once the home config is merged
    into the project config, that provenance is lost, so we bake each
    file's anchor in at load time.

    By the time merging runs, every path string is already absolute, so
    downstream callers (``_resolve_config_path``, ``_search_dirs``) can
    treat them uniformly regardless of which config they came from.

    No-ops on non-string values — type errors are routed through
    ``_validate_field_types`` and ``health_error()`` instead.
    """
    for field_name in _PATH_FIELDS:
        raw = cfg.get(field_name)
        if not isinstance(raw, str) or not raw:
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = anchor / p
        cfg[field_name] = str(p)


def _preflight_profile(profile_path: Path, workspace_root: Path) -> Optional[str]:
    """Ask sandbox-exec to actually load the profile; surface parse errors.

    Checking ``profile_path.is_file()`` only proves the file exists — a
    profile with invalid TinyScheme syntax would still slip through and then
    blow up on every real ``run_shell_command`` with ``Invalid sandbox
    profile``. This runs sandbox-exec against the profile with a trivial
    no-op command (``/bin/sh -c :``) so malformed profiles surface in
    ``/sandbox status`` / ``/sandbox on`` before the LLM can hit them.

    Returns ``None`` on clean load, otherwise a short human-readable reason.
    Callers must gate this behind ``platform_supported`` — sandbox-exec
    only exists on macOS.
    """
    try:
        r = subprocess.run(
            [
                "sandbox-exec",
                "-D", f"_RW1={workspace_root}",
                "-f", str(profile_path),
                "/bin/sh", "-c", ":",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return "sandbox-exec not found on PATH"
    except subprocess.TimeoutExpired:
        return "sandbox-exec preflight timed out after 5s"
    except OSError as e:
        return f"sandbox-exec preflight failed: {e}"
    if r.returncode == 0:
        return None
    # Parse/compile errors land on stderr before sandbox-exec ever forks.
    # A syntactically-valid profile running `:` cannot fail from the child
    # side, so any non-zero exit here is a profile-level problem.
    stderr = (r.stderr or "").strip()
    if not stderr:
        stderr = f"sandbox-exec exited with code {r.returncode}"
    return stderr


def _validate_field_types(cfg: Dict[str, Any], source: Path) -> List[str]:
    """Return a list of error strings for any field with a wrong JSON type.

    We can't trust user-editable config — `{"profiles_dir": 123}` is
    syntactically valid JSON but would crash `Path()` downstream. These
    errors are routed through the same `_load_errors` channel as JSON
    parse failures so the whole stack fails-closed uniformly.
    """
    errors: List[str] = []
    for field, allowed in _FIELD_TYPES.items():
        if field not in cfg:
            continue
        value = cfg[field]
        if not isinstance(value, allowed):
            expected = "/".join(
                t.__name__ if t is not type(None) else "null" for t in allowed
            )
            errors.append(
                f"{source}: field '{field}' must be {expected} "
                f"but got {type(value).__name__}"
            )
    return errors


def load_sandbox_config(*, project_root: Optional[Path] = None) -> Dict[str, Any]:
    """Load and merge sandbox config from home + project locations.

    Project config overrides user config key-by-key (shallow merge). An
    explicit `null` in the project file IS applied — this is how a
    project can reset an inherited user-level `profiles_dir` or
    `workspace_root` back to the documented default of `None`.

    Relative path fields (``profiles_dir``, ``workspace_root``) are
    resolved *before* merging, each against its own config file's anchor:

      - home config ``~/.agentao/sandbox.json``  →  ``~/.agentao/``
      - project config ``<proj>/.agentao/sandbox.json``  →  ``<proj>/``

    Without this, a global `"profiles_dir": "profiles"` would be searched
    under whichever project later consumed the merged dict, completely
    bypassing the shared user-level profiles directory.

    If either file is present on disk but unreadable / malformed, the
    merged config carries a `_load_errors` list so SandboxPolicy can
    fail-closed instead of quietly falling back to the default-disabled
    config.
    """
    global_path = Path.home() / ".agentao" / "sandbox.json"
    project_cwd = project_root if project_root is not None else Path.cwd()
    project_path = project_cwd / ".agentao" / "sandbox.json"

    global_cfg, g_err = _load_json(global_path)
    project_cfg, p_err = _load_json(project_path)

    # Collect parse + type-validation errors so misconfigurations fail
    # closed through the same channel.
    errors: List[str] = []
    if g_err:
        errors.append(g_err)
    else:
        errors.extend(_validate_field_types(global_cfg, global_path))
        _absolutize_path_fields(global_cfg, global_path.parent)
    if p_err:
        errors.append(p_err)
    else:
        errors.extend(_validate_field_types(project_cfg, project_path))
        _absolutize_path_fields(project_cfg, project_cwd)

    merged: Dict[str, Any] = dict(_DEFAULT_CONFIG)
    for cfg in (global_cfg, project_cfg):
        # Apply every key the user wrote, including explicit `null` values.
        # Skipping `null` would prevent a project file from clearing an
        # inherited user-level `profiles_dir` back to the default.
        for k, v in cfg.items():
            merged[k] = v

    if errors:
        merged[_PARSE_ERRORS_KEY] = errors
    return merged


def _is_platform_supported(config_platform: str) -> bool:
    """The sandbox backend currently only supports macOS."""
    return sys.platform == "darwin" and config_platform == "darwin"


class SandboxPolicy:
    """Resolves a sandbox profile for a given tool call.

    Construct with either a fixed ``project_root`` (ACP sessions with a
    frozen cwd) or a ``project_root_provider`` callable (legacy CLI path
    where the rest of the runtime tracks ``Path.cwd()`` live — the sandbox
    policy must do the same or a `cd` between turns will silently apply
    the old project's profile).

    Relative paths in the JSON config (``workspace_root``, ``profiles_dir``)
    are resolved against the live project root, NOT process cwd.
    """

    def __init__(
        self,
        *,
        project_root: Optional[Path] = None,
        project_root_provider: Optional[Callable[[], Path]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        if project_root is not None and project_root_provider is not None:
            raise ValueError(
                "Pass project_root OR project_root_provider, not both"
            )

        if project_root_provider is not None:
            # Dynamic root: re-read disk config on every cwd drift.
            self._project_root_provider: Callable[[], Path] = project_root_provider
            self._static_disk_config: Optional[Dict[str, Any]] = None
        elif config is not None:
            # Fully explicit static construction (mostly used by tests).
            static = project_root if project_root is not None else Path.cwd()
            self._project_root_provider = lambda r=static: r
            self._static_disk_config = config
        else:
            # Static disk-loaded config at construction time.
            static = project_root if project_root is not None else Path.cwd()
            self._project_root_provider = lambda r=static: r
            self._static_disk_config = load_sandbox_config(project_root=static)

        # Session-level toggles (/sandbox on / profile X) live here so they
        # survive cwd drift — the user's "I enabled this for my session"
        # intent shouldn't vanish because the process chdir'd.
        self._session_overrides: Dict[str, Any] = {}

        # Cache for dynamic-provider mode so we don't re-parse JSON on every
        # resolve() call unless the cwd actually changed.
        self._cached_for_root: Optional[Path] = None
        self._cached_disk_config: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Live config accessors
    # ------------------------------------------------------------------

    @property
    def _project_root(self) -> Path:
        """Current project root (follows live cwd when a provider is used)."""
        return self._project_root_provider()

    def _disk_config(self) -> Dict[str, Any]:
        """Disk-loaded config for the current project root (cached per root)."""
        if self._static_disk_config is not None:
            return self._static_disk_config
        current = self._project_root
        if self._cached_for_root != current:
            self._cached_disk_config = load_sandbox_config(project_root=current)
            self._cached_for_root = current
        return self._cached_disk_config

    @property
    def _config(self) -> Dict[str, Any]:
        """Merged live config: disk + session overrides.

        This is a property (not an attribute) so all call sites automatically
        pick up cwd drift and session toggles. Everything downstream — the
        `enabled` getter, `resolve()`, path helpers — reads this.
        """
        return {**self._disk_config(), **self._session_overrides}

    def _resolve_config_path(self, raw: Any) -> Optional[Path]:
        """Resolve a user-supplied path against the owning project, not cwd.

        Relative paths in ``sandbox.json`` should mean "relative to the
        project that owns this config file" — not "whichever directory the
        host process happens to be in right now". Absolute and ``~``-prefixed
        paths are left to stdlib rules.

        Returns None for non-string inputs. Load-time validation already
        converts these to `_load_errors`, but we defend in depth so the
        `/sandbox status` CLI can never crash on a bad-typed config that
        somehow bypassed validation (e.g., tests constructing the policy
        with a pre-built config dict).
        """
        if not isinstance(raw, str):
            return None
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = self._project_root / p
        return p.resolve()

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True iff the policy will attempt to wrap shell commands.

        Precedence:
          1. `/sandbox off` session override → always False (escape hatch
             that must work even when disk config is broken).
          2. Disk config has parse / validation errors → True on macOS
             (fail-closed). Checked BEFORE the platform-support test
             because a malformed `platform` field (e.g. `"platform": 123`)
             is itself one of those errors — testing platform first would
             read the bad value, return False, and silently disable
             sandboxing for exactly the misconfiguration we must block.
             On non-macOS returns False: we can't actually sandbox there,
             so blocking shell execution with no recovery wouldn't help.
          3. `/sandbox on` session override → True iff platform supports it.
          4. Unsupported platform (clean config) → False.
          5. Disk-level `enabled` value.
        """
        cfg = self._config
        override = self._session_overrides.get("enabled")

        if override is False:
            return False

        if cfg.get(_PARSE_ERRORS_KEY):
            return sys.platform == "darwin"

        if not _is_platform_supported(cfg.get("platform", "darwin")):
            return False
        if override is True:
            return True
        return bool(cfg.get("enabled"))

    @property
    def platform_supported(self) -> bool:
        return _is_platform_supported(self._config.get("platform", "darwin"))

    @property
    def config(self) -> Dict[str, Any]:
        """Read-only view of the merged config."""
        return dict(self._config)

    @property
    def default_profile_name(self) -> str:
        return str(self._config.get("default_profile") or "workspace-write-no-network")

    @property
    def workspace_root(self) -> Path:
        """Path passed as -D _RW1. Relative paths anchor to project root.

        Falls back to the live project root when `workspace_root` is unset
        OR has a non-string type (the type error is surfaced separately
        via ``health_error()``).
        """
        raw = self._config.get("workspace_root")
        if raw:
            resolved = self._resolve_config_path(raw)
            if resolved is not None:
                return resolved
        return self._project_root.resolve()

    # ------------------------------------------------------------------
    # Session-level toggles (used by /sandbox CLI commands; not persisted)
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self._session_overrides["enabled"] = bool(enabled)

    def set_default_profile(self, name: str) -> None:
        self._session_overrides["default_profile"] = name

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, tool_name: str, args: Dict[str, Any]) -> Optional[SandboxProfile]:
        """Return the profile that should wrap this tool call, or None.

        Returns None when no wrapping is needed:
          - policy is disabled
          - platform is not supported
          - tool is not `run_shell_command` (sandbox-exec is per-process
            and only meaningful for the shell tool)

        Raises `SandboxMisconfiguredError` when the policy IS enabled for a
        shell tool call but the requested profile name does not resolve to a
        file on disk. The caller MUST NOT fall back to unsandboxed execution
        — that would silently strip user-requested protection.
        """
        if not self.enabled:
            return None
        if tool_name != "run_shell_command":
            return None

        # Config file exists but we couldn't parse it — we have no idea
        # which profile to use. Fail-closed loudly so the user fixes it.
        parse_errors = self._config.get(_PARSE_ERRORS_KEY)
        if parse_errors:
            raise SandboxMisconfiguredError(
                "Sandbox config could not be parsed: "
                + "; ".join(parse_errors)
                + ". Fix the JSON or delete the file, then retry."
            )

        profile_name = self._profile_name_for(tool_name, args)
        profile_path = self._locate_profile(profile_name)
        if profile_path is None:
            avail = ", ".join(self.list_profiles()) or "(none)"
            raise SandboxMisconfiguredError(
                f"Sandbox is enabled but profile {profile_name!r} could not "
                f"be located. Available: {avail}. Fix `default_profile` / "
                f"`rules` / `profiles_dir` in .agentao/sandbox.json, or run "
                f"/sandbox off to proceed unsandboxed."
            )

        return SandboxProfile(
            name=profile_name,
            path=profile_path,
            workspace_root=self.workspace_root,
            params={"_RW1": str(self.workspace_root)},
        )

    # ------------------------------------------------------------------
    # Health / introspection (for CLI feedback)
    # ------------------------------------------------------------------

    def health_error(self) -> Optional[str]:
        """Return a human-readable reason why the sandbox would fail, else None.

        Probes the resolution path that `run_shell_command` would take. Used
        by `/sandbox status` and `/sandbox on` to refuse or warn before the
        LLM hits the same error at tool-call time.

        Unparseable config files always produce an error, even when the
        user-visible `enabled` field was never set, because we cannot know
        whether the user wanted sandboxing and must fail-closed.
        """
        parse_errors = self._config.get(_PARSE_ERRORS_KEY)
        if parse_errors:
            return "; ".join(parse_errors)
        if not self._config.get("enabled"):
            return None
        if not self.platform_supported:
            return f"platform is not macOS (current: {sys.platform!r})"
        name = self._profile_name_for("run_shell_command", {})
        return self.profile_health_error(name)

    def profile_health_error(self, name: str) -> Optional[str]:
        """Return why `name` can't be used as the active profile, else None.

        Runs the same two-step check as `health_error()` (file exists AND
        sandbox-exec can load it), but for an arbitrary candidate name.
        Used by `/sandbox profile <name>` so a malformed custom profile
        is rejected at the moment of switching — otherwise the CLI would
        report success and every subsequent `run_shell_command` would
        explode with "Invalid sandbox profile".

        Skips the sandbox-exec preflight on non-macOS: the binary doesn't
        exist there, and we can't sandbox anyway, so file-existence is
        the most we can verify.
        """
        path = self._locate_profile(name)
        if path is None:
            avail = ", ".join(self.list_profiles()) or "(none)"
            return f"profile '{name}' not found (available: {avail})"
        if not self.platform_supported:
            return None
        compile_error = _preflight_profile(path, self.workspace_root)
        if compile_error is not None:
            return f"profile '{name}' failed to load: {compile_error}"
        return None

    def rule_profile_for(self, tool_name: str) -> Optional[str]:
        """Return the profile a per-tool rule would pick, else None.

        Used by `/sandbox profile <name>` to warn when a rule shadows the
        default_profile the user is trying to switch to.
        """
        rules = self._config.get("rules") or []
        if not isinstance(rules, list):
            return None
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if rule.get("tool") == tool_name and rule.get("profile"):
                return str(rule["profile"])
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _profile_name_for(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Pick a profile name: first matching rule wins, else default."""
        rules = self._config.get("rules") or []
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                if rule.get("tool") == tool_name and rule.get("profile"):
                    return str(rule["profile"])
        return self.default_profile_name

    def _search_dirs(self) -> List[Path]:
        """Profile search path: user-configured dir first, then built-ins.

        User-configured paths are resolved against the project root so a
        project-local `sandbox.json` can use relative paths without getting
        silently redirected to the host process's cwd.
        """
        out: List[Path] = []
        user_dir = self._config.get("profiles_dir")
        if user_dir:
            resolved = self._resolve_config_path(user_dir)
            if resolved is not None:
                out.append(resolved)
            # else: wrong type in config — ignore rather than crash;
            # the type error is already surfaced via health_error().
        out.append(_BUILTIN_PROFILES_DIR)
        return out

    def _locate_profile(self, name: str) -> Optional[Path]:
        """Find <name>.sb in user profiles_dir (first) then built-in dir."""
        filename = name if name.endswith(".sb") else f"{name}.sb"
        for d in self._search_dirs():
            candidate = d / filename
            if candidate.is_file():
                return candidate.resolve()
        return None

    def list_profiles(self) -> List[str]:
        """List profile names available to this policy (for `/sandbox` CLI)."""
        names: List[str] = []
        seen = set()
        for d in self._search_dirs():
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.sb")):
                if f.stem not in seen:
                    names.append(f.stem)
                    seen.add(f.stem)
        return names
