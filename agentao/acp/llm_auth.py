"""Terminal Auth advertisement and per-session LLM resolution for the ACP server.

Agentao's "authentication" is its LLM provider configuration. A client that
can run a terminal launches ``agentao --login`` (appended to, or in place of,
its usual launch args) as a separate process; that writes
``~/.agentao/llm.json`` and exits, and the client reconnects. Nothing about
the login travels over the ACP connection — the spec forbids a client from
calling ``authenticate`` for a ``terminal`` method, so Agentao advertises no
``authenticate`` handler either.

Two capability spellings are honoured, because the clients disagree:

- ``clientCapabilities.auth.terminal`` — the ACP v1 field. DeepChat and Brokk
  send only this.
- ``clientCapabilities._meta["terminal-auth"]`` — the pre-stabilization
  extension. The ACP Registry's ``--auth-check`` validator sends only this,
  so an agent that ignored it would fail the listing check.

Each counts only when it is the boolean ``true``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Mapping

from agentao import __version__ as AGENTAO_VERSION
from agentao.embedding.llm_config import (
    LLMConfigError,
    ResolvedLLMConfig,
    resolve_session_llm_config,
)

from .protocol import AUTH_REQUIRED, INTERNAL_ERROR
from .server import JsonRpcHandlerError

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)

TERMINAL_AUTH_METHOD_ID = "agentao-login"

#: The flag the login process is launched with. Shared with the CLI parser.
LOGIN_FLAG = "--login"

TERMINAL_AUTH_METHOD: Dict[str, Any] = {
    "id": TERMINAL_AUTH_METHOD_ID,
    "name": "Configure LLM provider",
    "description": (
        "Choose a provider and enter its endpoint, model and API key in a "
        "terminal. Saved to ~/.agentao/llm.json."
    ),
    "type": "terminal",
    "args": [LOGIN_FLAG],
}


def client_supports_terminal_auth(client_capabilities: Mapping[str, Any]) -> bool:
    """Whether the client declared Terminal Auth, in either spelling."""
    auth = client_capabilities.get("auth")
    if isinstance(auth, dict) and auth.get("terminal") is True:
        return True
    meta = client_capabilities.get("_meta")
    return isinstance(meta, dict) and meta.get("terminal-auth") is True


def auth_methods_for(client_capabilities: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The ``authMethods`` for an ``initialize`` response.

    Empty for a client that cannot run a terminal method: the spec allows an
    agent to advertise one only when the client declared support.
    """
    if client_supports_terminal_auth(client_capabilities):
        return [dict(TERMINAL_AUTH_METHOD, args=list(TERMINAL_AUTH_METHOD["args"]))]
    return []


def login_command() -> str:
    """A login command that works without ``agentao`` on ``PATH``.

    A Registry install runs through ``uvx``, so the user has no ``agentao``
    executable to type; this is the same package spec the Registry entry
    launches. ``--login`` needs none of the ``[cli]`` extras.
    """
    return f"uvx agentao@{AGENTAO_VERSION} {LOGIN_FLAG}"


def _auth_required_message(
    resolved: ResolvedLLMConfig, client_capabilities: Mapping[str, Any]
) -> str:
    missing = ", ".join(resolved.missing_fields())
    head = (
        f"Agentao has no usable LLM provider configuration "
        f"(provider {resolved.provider_id!r}, missing: {missing})."
    )
    if client_supports_terminal_auth(client_capabilities):
        return f"{head} Run the 'Configure LLM provider' login, then reconnect."
    prefix = resolved.provider.upper()
    return (
        f"{head} Run `{login_command()}` in a terminal (or set "
        f"{prefix}_API_KEY, {prefix}_BASE_URL and {prefix}_MODEL in the "
        f"launch environment or the project .env), then reconnect."
    )


def resolve_session_llm(server: "AcpServer", cwd: Path) -> ResolvedLLMConfig:
    """Resolve a new or loaded session's LLM configuration.

    Raises :class:`JsonRpcHandlerError`: ``AUTH_REQUIRED`` when a required
    field is missing everywhere, ``INTERNAL_ERROR`` when a source exists but
    is unusable — a broken file is not something a login prompt can fix, and
    reporting it as one would send the user round a loop.
    """
    try:
        resolved = resolve_session_llm_config(cwd, launch_env=server.launch_env)
        if not resolved.missing_fields():
            # Surface a malformed LLM_TEMPERATURE etc. here, as a config error,
            # rather than from inside the agent constructor as a bare -32603.
            resolved.llm_kwargs()
    except LLMConfigError as exc:
        logger.warning("acp: LLM configuration error: %s", exc)
        raise JsonRpcHandlerError(
            code=INTERNAL_ERROR,
            message=f"Agentao LLM configuration error: {exc}",
        ) from None
    if resolved.missing_fields():
        raise JsonRpcHandlerError(
            code=AUTH_REQUIRED,
            message=_auth_required_message(resolved, server.state.client_capabilities),
        )
    return resolved
