"""Sandbox policy: decides whether to wrap a tool call in sandbox-exec.

On macOS, when enabled, `run_shell_command` is wrapped in a sandbox-exec
subprocess that restricts filesystem writes (and optionally network) based
on a named profile. The policy is a strict *added* layer — it runs after
the permission engine already decided ALLOW.

Config source: ``~/.agentao/sandbox.json`` only. A project-level
``<project>/.agentao/sandbox.json`` is ignored with a warning, for the same
reason ``embedding/permission_loader.py`` ignores a project
``permissions.json``: a checked-in file (or a write by a sandboxed command,
which can write the whole workspace) could switch the sandbox off or swap
its profile. Session toggles (``/sandbox on|off|profile``) still apply.

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
import logging
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..paths import user_root
from .. import json_parse


_logger = logging.getLogger(__name__)

_BUILTIN_PROFILES_DIR = Path(__file__).parent / "profiles"


def _load_builtin_profiles() -> Tuple[Dict[str, str], Optional[str]]:
    """Read every built-in ``.sb`` once; return ``(name -> text, error)``."""
    try:
        texts = {
            f.stem: f.read_text(encoding="utf-8")
            for f in sorted(_BUILTIN_PROFILES_DIR.glob("*.sb"))
        }
    except (OSError, UnicodeDecodeError) as e:
        return {}, f"the built-in sandbox profiles could not be read: {e}"
    if not texts:
        return {}, f"no built-in sandbox profiles found in {_BUILTIN_PROFILES_DIR}"
    return texts, None


# The built-in profiles, read once at import and never again. A sandboxed
# command can write the installed package (a project ``.venv`` is inside the
# workspace), so a profile read from disk per command could be rewritten by
# the command before it. The snapshot is passed inline (``-p``), and nothing
# falls back to the files: a failed read is an error, not a disk lookup.
_BUILTIN_PROFILES, _BUILTIN_PROFILES_ERROR = _load_builtin_profiles()

# macOS ships sandbox-exec here. Never resolve it through PATH: a sandboxed
# command can write a directory that is on PATH (``.venv/bin``,
# ``node_modules/.bin``) and plant a ``sandbox-exec`` that drops the profile
# for every later command.
SANDBOX_EXEC = "/usr/bin/sandbox-exec"


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


_SB_COMMENT = re.compile(r";[^\n]*")
_SB_DENY_NETWORK = re.compile(r"\(\s*deny\s+network")
_SB_DENY_DEFAULT = re.compile(r"\(\s*deny\s+default\s*\)")
_SB_ALLOW_NETWORK = re.compile(r"\(\s*allow\s+network")


def profile_denies_network(path: Path) -> bool:
    """Whether the ``.sb`` profile at ``path`` blocks network access.

    True for a ``(deny network…)`` rule, or for ``(deny default)`` with no
    ``(allow network…)`` rule. Comments are ignored. A profile that cannot
    be read counts as not blocking: this only decides whether a network
    error gets the sandbox hint, and a wrong hint misleads more than a
    missing one.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return _text_denies_network(text)


def _text_denies_network(text: str) -> bool:
    """:func:`profile_denies_network`, over the profile's text."""
    text = _SB_COMMENT.sub("", text)
    if _SB_DENY_NETWORK.search(text):
        return True
    return bool(_SB_DENY_DEFAULT.search(text)) and not _SB_ALLOW_NETWORK.search(text)


def _sbpl_string(value: str) -> str:
    """Quote ``value`` as an SBPL string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _path_forms(path: Path) -> List[str]:
    """``path`` as written and with symlinks resolved, deduplicated.

    The sandbox matches the real path (``/private/tmp``, not ``/tmp``), and the
    workspace parameter is passed as written, so a deny rule names both.
    """
    forms = [str(path)]
    real = os.path.realpath(path)
    if real not in forms:
        forms.append(real)
    return forms


_SBPL_REGEX_SPECIAL = set(".^$*+?()[]{}|\\")

def _sb_files_regex(directory: str) -> str:
    """An SBPL regex for any ``*.sb`` file directly in ``directory``."""
    escaped = "".join("\\" + c if c in _SBPL_REGEX_SPECIAL else c for c in directory)
    return f"^{escaped}/[^/]*\\.sb$"


def _location(path: Path) -> Path:
    """``path`` with its parent resolved but its last component kept.

    For a symlink this is where the link itself lives, which a command can
    unlink and replace even when the link's target is out of reach.
    """
    return Path(os.path.realpath(path.parent)) / path.name


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class SandboxProfile:
    """Resolved sandbox profile ready to be applied to a subprocess."""
    name: str
    #: The profile file. For a built-in profile this is display only: what
    #: runs is ``text``, the snapshot taken at import.
    path: Path
    workspace_root: Path
    params: Dict[str, str] = field(default_factory=dict)
    # Read from the profile when it is resolved; see ``profile_denies_network``.
    denies_network: bool = False
    #: Paths no sandboxed write may touch, even inside the workspace: directory
    #: trees (``subpath``) and single paths (``literal``). See
    #: ``SandboxPolicy._protected_paths``.
    protected_subpaths: Tuple[str, ...] = ()
    protected_literals: Tuple[str, ...] = ()
    #: SBPL regexes (``regex``), for files that may not exist yet.
    protected_regexes: Tuple[str, ...] = ()
    #: A built-in profile's text, passed inline instead of ``path``.
    text: Optional[str] = None

    def as_args(self) -> List[str]:
        """Return argv *prefix* for sandbox-exec (without the wrapped command).

        Example output::

            ["/usr/bin/sandbox-exec", "-D", "_RW1=/Users/me/proj", "-p", "<profile>"]

        The profile is always passed inline (``-p``): a built-in profile as its
        snapshot ``text``, a user profile as an ``(import "<path>")``. Then a
        ``(deny file-write* …)`` rule covers the protected paths. A later rule
        wins in SBPL, so the deny holds over the profile's own ``allow``.
        """
        out: List[str] = [SANDBOX_EXEC]
        for k, v in self.params.items():
            out.extend(["-D", f"{k}={v}"])
        if self.text is not None:
            body = self.text.rstrip("\n") + "\n"
        else:
            body = f"(version 1)\n(import {_sbpl_string(str(self.path))})\n"
        filters = [f"(subpath {_sbpl_string(p)})" for p in self.protected_subpaths]
        filters += [f"(literal {_sbpl_string(p)})" for p in self.protected_literals]
        filters += [f"(regex {_sbpl_string(p)})" for p in self.protected_regexes]
        if filters:
            body += f"(deny file-write* {' '.join(filters)})\n"
        out.extend(["-p", body])
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
        data = json_parse.loads(raw)
    except json.JSONDecodeError as e:
        return {}, f"{path}: invalid JSON (line {e.lineno}, col {e.colno}: {e.msg})"
    if not isinstance(data, dict):
        return {}, f"{path}: root must be a JSON object, got {type(data).__name__}"
    return data, None


# Sentinel key used to smuggle parse errors through the returned config so
# SandboxPolicy can fail-closed. NOT a documented public config field.
_PARSE_ERRORS_KEY = "_load_errors"

# Sentinel key carrying the path of an ignored project-level file, so
# ``/sandbox status`` can say why it has no effect. NOT a config field.
_IGNORED_PROJECT_KEY = "_ignored_project_config"


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
    project the agent happens to load next". The policy later resolves a
    relative path against the project root, so we bake the file's anchor in
    at load time.

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


def _preflight_profile(profile: SandboxProfile) -> Optional[str]:
    """Ask sandbox-exec to actually load the profile; surface parse errors.

    Checking that the profile exists proves nothing about its syntax — a
    profile with invalid TinyScheme would still slip through and then blow
    up on every real ``run_shell_command`` with ``Invalid sandbox profile``.
    This runs the exact argv a command would get (:meth:`SandboxProfile.as_args`,
    deny rule included) with a no-op command (``/bin/sh -c :``), so a
    malformed profile surfaces in ``/sandbox status`` / ``/sandbox on`` before
    the LLM can hit it.

    Returns ``None`` on clean load, otherwise a short human-readable reason.
    Callers must gate this behind ``platform_supported`` — sandbox-exec
    only exists on macOS.
    """
    try:
        r = subprocess.run(
            profile.as_args() + ["/bin/sh", "-c", ":"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return f"{SANDBOX_EXEC} not found"
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
    for field_name, allowed in _FIELD_TYPES.items():
        if field_name not in cfg:
            continue
        value = cfg[field_name]
        if not isinstance(value, allowed):
            expected = "/".join(
                t.__name__ if t is not type(None) else "null" for t in allowed
            )
            errors.append(
                f"{source}: field '{field_name}' must be {expected} "
                f"but got {type(value).__name__}"
            )
    return errors


def load_sandbox_config(*, project_root: Optional[Path] = None) -> Dict[str, Any]:
    """Load the sandbox config from ``~/.agentao/sandbox.json``.

    A project-level ``<project>/.agentao/sandbox.json`` is never honored.
    When one exists, a warning is logged and its path is carried under
    ``_ignored_project_config`` so the CLI can show it. The file is not
    parsed: a malformed project file can no longer change anything, so it
    has no reason to fail the sandbox closed either.

    A relative path field (``profiles_dir``, ``workspace_root``) is resolved
    against ``~/.agentao/`` at load time. Without this, a user-level
    ``"profiles_dir": "profiles"`` would be searched under whichever project
    later consumed the dict.

    If the user file is present but unreadable or malformed, the config
    carries a ``_load_errors`` list so SandboxPolicy can fail closed instead
    of quietly falling back to the default-disabled config.
    """
    global_path = user_root() / "sandbox.json"
    project_cwd = project_root if project_root is not None else Path.cwd()

    global_cfg, g_err = _load_json(global_path)

    # Collect parse + type-validation errors so misconfigurations fail
    # closed through the same channel.
    errors: List[str] = []
    if g_err:
        errors.append(g_err)
    else:
        errors.extend(_validate_field_types(global_cfg, global_path))
        _absolutize_path_fields(global_cfg, global_path.parent)

    merged: Dict[str, Any] = dict(_DEFAULT_CONFIG)
    # Apply every key the user wrote, including explicit `null` values.
    merged.update(global_cfg)

    if errors:
        merged[_PARSE_ERRORS_KEY] = errors
    project_path = ignored_project_sandbox_config(project_cwd)
    if project_path is not None:
        _logger.warning(
            "Ignoring %s: project-scope sandbox config is no longer honored "
            "(a checked-in or agent-written file could switch the sandbox "
            "off). Move settings to %s.",
            project_path,
            global_path,
        )
        merged[_IGNORED_PROJECT_KEY] = str(project_path)
    return merged


def ignored_project_sandbox_config(project_root: Path) -> Optional[Path]:
    """Path of the project-level sandbox.json the loader ignores, else None.

    ``samefile`` guards the case where the project root IS the home
    directory: then the "project" file is the user file, and it is honored.
    ``agentao doctor`` calls this too, so both report the same file.
    """
    project_path = project_root / ".agentao" / "sandbox.json"
    if not project_path.exists():
        return None
    if _same_file(project_path, user_root() / "sandbox.json"):
        return None
    return project_path


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


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

    A relative ``workspace_root`` / ``profiles_dir`` in a config passed as
    ``config=`` is resolved against the live project root, NOT process cwd.
    The disk loader has already made the user file's paths absolute.
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
        """Resolve a configured path against the project root, not cwd.

        A relative path in a ``config=`` dict means "relative to the
        project" — not "whichever directory the host process happens to be
        in right now". Absolute and ``~``-prefixed paths are left to stdlib
        rules.

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
    def ignored_project_config(self) -> Optional[Path]:
        """Path of a project-level sandbox.json that was ignored, else None."""
        raw = self._config.get(_IGNORED_PROJECT_KEY)
        return Path(raw) if isinstance(raw, str) else None

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
        profile = self._build_profile(profile_name)
        if profile is None:
            raise SandboxMisconfiguredError(self._not_found_message(profile_name))
        return profile

    def _not_found_message(self, name: str) -> str:
        if _BUILTIN_PROFILES_ERROR is not None and self._user_profile(name) is None:
            return f"Sandbox is enabled but {_BUILTIN_PROFILES_ERROR}."
        avail = ", ".join(self.list_profiles()) or "(none)"
        return (
            f"Sandbox is enabled but profile {name!r} could not "
            f"be located. Available: {avail}. Fix `default_profile` / "
            f"`rules` / `profiles_dir` in ~/.agentao/sandbox.json, or run "
            f"/sandbox off to proceed unsandboxed."
        )

    def _build_profile(self, name: str) -> Optional[SandboxProfile]:
        """The profile ``name`` as a command would run it, or None if not found."""
        user_path = self._user_profile(name)
        if user_path is not None:
            path, text = user_path, None
            denies_network = profile_denies_network(user_path)
        else:
            stem = name[:-3] if name.endswith(".sb") else name
            text = _BUILTIN_PROFILES.get(stem)
            if text is None:
                return None
            path = _BUILTIN_PROFILES_DIR / f"{stem}.sb"
            denies_network = _text_denies_network(text)
        workspace = self.workspace_root
        subpaths, literals, regexes = self._protected_paths(workspace)
        return SandboxProfile(
            name=name,
            path=path,
            workspace_root=workspace,
            params={"_RW1": str(workspace)},
            denies_network=denies_network,
            protected_subpaths=subpaths,
            protected_literals=literals,
            protected_regexes=regexes,
            text=text,
        )

    def _protected_paths(
        self, workspace: Path
    ) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
        """What a sandboxed write must never touch.

        The sandbox's own configuration: the user config directory
        (``~/.agentao``, which holds ``sandbox.json`` and also
        ``permissions.json`` and ``mcp.json``, read unsandboxed later) and a
        user ``profiles_dir``. A command that can rewrite them can turn the
        sandbox off, or weaken a profile, for every later command. Denied
        whatever the workspace, so it does not matter which paths a profile
        allows (``/tmp``, a custom profile's own subpaths).

        - The directory is denied whole (``subpath``). Inside the workspace,
          so is each directory between it and the workspace (``literal``),
          so it cannot be renamed away and replaced.
        - When the workspace is the directory or lies inside it, denying the
          tree would deny the workspace itself, so only the files that
          configure the sandbox are denied: ``sandbox.json`` and any ``.sb``
          file there, including one not yet written (a new ``.sb`` in a
          ``profiles_dir`` would shadow a built-in profile).
        - A symlinked directory is also denied where the link lives, so the
          link cannot be replaced by a directory the command controls.

        The built-in profiles are not on the list: they run from the snapshot
        taken at import, so rewriting their files changes nothing in this
        process, and denying them would make a project ``.venv`` that holds
        agentao impossible to delete or upgrade under the sandbox.
        """
        ws = Path(os.path.realpath(workspace))
        subpaths: List[str] = []
        literals: List[str] = []
        regexes: List[str] = []

        def deny_ancestors(path: Path) -> None:
            for parent in path.parents:
                if parent == ws or not _is_within(parent, ws):
                    break
                literals.extend(_path_forms(parent))

        roots = [user_root()]
        user_dir = self._user_profiles_dir()
        if user_dir is not None:
            roots.append(user_dir)
        for root in roots:
            real_root = Path(os.path.realpath(root))
            link = _location(root)
            if link != real_root and link != ws:
                literals.append(str(link))
                deny_ancestors(link)
            if _is_within(ws, real_root):
                literals.extend(_path_forms(root / "sandbox.json"))
                regexes.extend(_sb_files_regex(f) for f in _path_forms(root))
            else:
                subpaths.extend(_path_forms(root))
                deny_ancestors(real_root)
        return (
            tuple(dict.fromkeys(subpaths)),
            tuple(dict.fromkeys(literals)),
            tuple(dict.fromkeys(regexes)),
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
        profile = self._build_profile(name)
        if profile is None:
            if _BUILTIN_PROFILES_ERROR is not None and self._user_profile(name) is None:
                return _BUILTIN_PROFILES_ERROR
            avail = ", ".join(self.list_profiles()) or "(none)"
            return f"profile '{name}' not found (available: {avail})"
        if not self.platform_supported:
            return None
        compile_error = _preflight_profile(profile)
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

    def _user_profiles_dir(self) -> Optional[Path]:
        """The configured ``profiles_dir``, searched before the built-ins.

        A relative configured path is resolved against the project root,
        never the host process's cwd.
        """
        user_dir = self._config.get("profiles_dir")
        if not user_dir:
            return None
        # ``None`` for a wrong type in config — ignored rather than crashing;
        # the type error is already surfaced via health_error().
        return self._resolve_config_path(user_dir)

    def _user_profile(self, name: str) -> Optional[Path]:
        """``<name>.sb`` in the user ``profiles_dir``, else None."""
        user_dir = self._user_profiles_dir()
        if user_dir is None:
            return None
        filename = name if name.endswith(".sb") else f"{name}.sb"
        candidate = user_dir / filename
        return candidate.resolve() if candidate.is_file() else None

    def _locate_profile(self, name: str) -> Optional[Path]:
        """Where ``<name>.sb`` comes from: the user ``profiles_dir`` first, then
        the built-in snapshot (shown as its file path, never read again)."""
        profile = self._build_profile(name)
        return profile.path if profile is not None else None

    def list_profiles(self) -> List[str]:
        """List profile names available to this policy (for `/sandbox` CLI)."""
        names: List[str] = []
        user_dir = self._user_profiles_dir()
        if user_dir is not None and user_dir.is_dir():
            names.extend(f.stem for f in sorted(user_dir.glob("*.sb")))
        names.extend(n for n in _BUILTIN_PROFILES if n not in names)
        return names
