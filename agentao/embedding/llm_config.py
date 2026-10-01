"""The user's login file, and the per-session LLM resolution the ACP server uses.

Two halves, one file, because the second reads what the first writes.

**The login file** — ``~/.agentao/llm.json``, written by ``agentao --login``
(a Terminal Auth login process an ACP client launches; see
:mod:`agentao.cli.login`). One provider block: ``provider``, ``api_key``,
``base_url``, ``model`` and optionally ``api_format``. It is written with mode
``0600`` through a temp file in the same directory, so a reader never sees a
half-written key.

**Per-session resolution** — :func:`resolve_session_llm_config`. The ACP
server is one process serving sessions from several projects, so it must not
do what :func:`~agentao.embedding.build_from_environment` does by default:
load a project's ``.env`` into ``os.environ`` without overriding. That made
the *first* session's project credentials stick for every later session, and
made a model switch (which read ``os.environ`` again) pick them up too. Here
each session gets its own resolution from three layers, highest first:

1. the server's **launch environment** (a snapshot taken at startup);
2. the session's **project** ``<cwd>/.env`` — read, never written anywhere,
   and never searched for above ``cwd``;
3. the **login file**.

An empty or whitespace-only value counts as unset in every layer (Claude Code
injects ``ANTHROPIC_API_KEY=""`` into its children; that must not mask a real
key further down).

The provider is chosen **before** any field is read: the first layer that
names one explicitly (``LLM_PROVIDER`` in the two env layers, ``provider`` in
the file) wins, and ``OPENAI`` is the default. Whether a layer *has an API
key* plays no part — a shell that exports ``OPENAI_API_KEY`` for some other
tool must not turn a DeepSeek login into an OpenAI session. The fields are
then read layer by layer, but only under that provider's prefix, and the file
contributes only when its ``provider`` is the chosen one, so one provider's
key is never paired with another's endpoint. The API key, the base URL and the
wire format are the exception to field-by-field: they come **together** from
the highest layer that sets a key. A key whose layer has no base URL leaves the
URL missing rather than borrowing one from below — otherwise a key exported for
another tool would be sent to the endpoint the user logged in with — and a
layer with no format means the default wire, not a lower layer's.

Only LLM settings come from the project ``.env`` on this path. Everything
else a ``.env`` used to put into the environment (MCP ``${VAR}`` expansion,
web-tool keys, ``GITHUB_TOKEN``, URL policy) has to come from the launch
environment — writing those into a shared process is exactly the leak this
module removes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from dotenv import dotenv_values
from dotenv.variables import parse_variables

from .factory import discover_llm_kwargs

USER_LLM_CONFIG_FILENAME = "llm.json"

#: The keys the login file may carry. Anything else is ignored, so a newer
#: agentao's file does not break an older one.
USER_CONFIG_FIELDS: Tuple[str, ...] = (
    "provider", "api_key", "base_url", "model", "api_format",
)

#: The fields :class:`~agentao.agent.Agentao` refuses to start without.
REQUIRED_FIELDS: Tuple[str, ...] = ("api_key", "base_url", "model")

DEFAULT_PROVIDER = "OPENAI"

# Login-file field → environment-variable suffix under the provider prefix.
_FILE_TO_SUFFIX = {
    "api_key": "API_KEY",
    "base_url": "BASE_URL",
    "model": "MODEL",
    "api_format": "API_FORMAT",
}

# The provider-agnostic keys :func:`discover_llm_kwargs` reads.
_SHARED_KEYS: Tuple[str, ...] = (
    "LLM_TEMPERATURE",
    "LLM_MAX_TOKENS",
    "LLM_PROMPT_CACHE",
    "LLM_PROMPT_CACHE_TTL",
    "LLM_EXTRA_BODY",
)


class LLMConfigError(ValueError):
    """A configuration source exists but cannot be used.

    Distinct from *missing* configuration, which callers report as an
    authentication problem. The message names the source and the problem,
    never a value — it reaches ACP clients.
    """


def user_llm_config_path(user_dir: Optional[Path] = None) -> Path:
    """``<user_dir>/llm.json``; ``user_dir`` defaults to ``~/.agentao``."""
    if user_dir is None:
        from ..paths import user_root

        user_dir = user_root()
    return user_dir / USER_LLM_CONFIG_FILENAME


def load_user_llm_config(path: Path) -> Optional[Dict[str, str]]:
    """Read the login file. ``None`` when it does not exist.

    Returns the known fields with surrounding whitespace removed; empty
    values are dropped. Raises :class:`LLMConfigError` when the file exists
    but is unreadable, is not a JSON object, or carries a non-string field.
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise LLMConfigError(f"cannot read {path} ({type(exc).__name__})") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMConfigError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from None
    if not isinstance(data, dict):
        raise LLMConfigError(f"{path} must contain a JSON object")
    out: Dict[str, str] = {}
    for key in USER_CONFIG_FIELDS:
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise LLMConfigError(f"{path}: field {key!r} must be a string")
        if value.strip():
            out[key] = value.strip()
    return out


def save_user_llm_config(
    path: Path,
    *,
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    api_format: Optional[str] = None,
) -> None:
    """Write the login file atomically with owner-only permissions.

    The temp file is created with mode ``0600`` and then ``chmod``-ed to it
    (the umask can only narrow the creation mode), so the key is never
    readable by others, not even briefly. On Windows the mode is a no-op and
    the file's protection is the per-user home directory it lives in.
    """
    data: Dict[str, Any] = {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }
    if api_format:
        data["api_format"] = api_format
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class ResolvedLLMConfig:
    """One session's LLM configuration, fixed at session creation.

    ``env`` holds only the chosen provider's keys and the shared ``LLM_*``
    keys, with empty values already dropped — the mapping
    :func:`~agentao.embedding.factory.discover_llm_kwargs` reads in place of
    ``os.environ``. A model switch resolves against this snapshot, so a later
    login does not change the credentials of a session that already exists.
    """

    provider: str
    env: Mapping[str, str] = field(repr=False)

    @property
    def provider_id(self) -> str:
        """The provider in the lower-cased wire form ``provider/model`` uses."""
        return self.provider.lower()

    def llm_kwargs(self) -> Dict[str, Any]:
        """The ``Agentao`` LLM kwargs. Raises :class:`LLMConfigError`."""
        for name, convert in (("LLM_TEMPERATURE", float), ("LLM_MAX_TOKENS", int)):
            if name in self.env:
                try:
                    convert(self.env[name])
                except ValueError:
                    raise LLMConfigError(f"{name} is not a valid number") from None
        return discover_llm_kwargs(self.env)

    def missing_fields(self) -> Tuple[str, ...]:
        """The required fields with no value in any layer."""
        prefix = self.provider.upper()
        return tuple(
            name for name in REQUIRED_FIELDS
            if not self.env.get(f"{prefix}_{_FILE_TO_SUFFIX[name]}")
        )

    def resolve_provider(self, provider_id: str) -> Dict[str, Optional[str]]:
        """A ``provider_resolver`` over this snapshot.

        Same contract as
        :func:`agentao.acp.session_set_config_option.default_provider_resolver`:
        only the session's own provider resolves, anything else raises
        :class:`LookupError`.
        """
        if provider_id.strip().lower() != self.provider_id:
            raise LookupError(
                f"provider {provider_id!r} is not the configured provider "
                f"({self.provider_id!r}); inject a provider_resolver to switch "
                "providers"
            )
        prefix = self.provider.upper()
        api_key = self.env.get(f"{prefix}_API_KEY")
        if not api_key:
            raise LookupError(
                f"no API key configured for provider {provider_id!r}"
            )
        return {
            "api_key": api_key,
            "base_url": self.env.get(f"{prefix}_BASE_URL"),
            "api_format": self.env.get(f"{prefix}_API_FORMAT"),
        }


def _set_values(mapping: Mapping[str, Any]) -> Dict[str, str]:
    """``mapping`` without its unset entries: ``None``, non-strings, blanks."""
    return {
        key: value.replace("\x00", "")
        for key, value in mapping.items()
        if isinstance(value, str) and value.strip()
    }


def _read_project_dotenv(project_root: Path, launch_env: Mapping[str, str]) -> Dict[str, str]:
    """The project ``.env``, with ``${VAR}`` expanded against ``launch_env``.

    ``dotenv_values`` would expand references against the live
    ``os.environ`` — the very thing the launch snapshot exists to avoid. So
    the file is parsed raw and expanded here with python-dotenv's own
    ``parse_variables``, keeping its semantics (a value defined earlier in the
    file wins over the environment; ``${VAR:-default}`` works) but reading the
    snapshot instead.
    """
    path = project_root / ".env"
    if not path.is_file():
        return {}
    try:
        raw = dotenv_values(path, interpolate=False)
    except (OSError, UnicodeDecodeError) as exc:
        raise LLMConfigError(f"cannot read {path} ({type(exc).__name__})") from None
    resolved: Dict[str, Optional[str]] = {}
    for name, value in raw.items():
        if value is None:
            resolved[name] = None
            continue
        scope: Dict[str, Optional[str]] = {**launch_env, **resolved}
        resolved[name] = "".join(atom.resolve(scope) for atom in parse_variables(value))
    return _set_values(resolved)


def resolve_session_llm_config(
    project_root: Path,
    *,
    launch_env: Mapping[str, str],
    user_config_path: Optional[Path] = None,
) -> ResolvedLLMConfig:
    """Resolve one session's LLM configuration. See the module docstring.

    Reads the project ``.env`` and the login file on every call — a login
    completed while the server is running is seen by the next session.
    Raises :class:`LLMConfigError` when a source exists but is unusable;
    incomplete configuration is not an error here (see
    :meth:`ResolvedLLMConfig.missing_fields`).
    """
    if user_config_path is None:
        user_config_path = user_llm_config_path()
    launch = _set_values(launch_env)
    project = _read_project_dotenv(project_root, launch_env)
    user = load_user_llm_config(user_config_path) or {}

    provider = (
        launch.get("LLM_PROVIDER")
        or project.get("LLM_PROVIDER")
        or user.get("provider")
        or DEFAULT_PROVIDER
    ).strip()
    prefix = provider.upper()

    user_layer: Dict[str, str] = {}
    if user.get("provider", DEFAULT_PROVIDER).upper() == prefix:
        user_layer = {
            f"{prefix}_{suffix}": user[name]
            for name, suffix in _FILE_TO_SUFFIX.items()
            if name in user
        }

    keys = {f"{prefix}_{suffix}" for suffix in _FILE_TO_SUFFIX.values()}
    keys.update(_SHARED_KEYS)
    env: Dict[str, str] = {}
    for layer in (user_layer, project, launch):  # lowest first; later wins
        env.update({key: value for key, value in layer.items() if key in keys})

    # The key and the endpoint it is sent to come from one layer: the highest
    # that sets a key. Merged field by field, a shell's OPENAI_API_KEY (for
    # some other tool) would outrank a login's key and be sent to the login's
    # gateway URL — a leak, then a 401 that logging in again cannot fix,
    # because the login file is the lowest layer.
    # The wire format goes with them too: it is a property of the endpoint, and
    # a login's ``anthropic-messages`` inherited by a project's OpenAI-compatible
    # gateway would send it Messages requests. A layer that sets no format
    # means the default wire, never a lower layer's.
    key_name = f"{prefix}_API_KEY"
    endpoint_names = (f"{prefix}_BASE_URL", f"{prefix}_API_FORMAT")
    for layer in (launch, project, user_layer):  # highest first
        if key_name in layer:
            env[key_name] = layer[key_name]
            for name in endpoint_names:
                if name in layer:
                    env[name] = layer[name]
                else:
                    env.pop(name, None)
            break

    env["LLM_PROVIDER"] = provider
    return ResolvedLLMConfig(provider=provider, env=env)
