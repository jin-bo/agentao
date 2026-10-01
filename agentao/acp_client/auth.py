"""ACP authentication on the client side: advertised methods and Terminal Auth.

An ACP agent lists its ways to authenticate in the ``initialize`` response's
``authMethods``, and answers a request that needs credentials with
``auth_required`` (JSON-RPC ``-32000``). This module keeps the pieces of that
contract that do not depend on how a host talks to its user:

- :func:`normalize_auth_methods` — what is kept from ``authMethods``;
- :func:`is_auth_required` — whether a failure is ``auth_required``;
- :func:`build_terminal_login_command` — the process a ``terminal`` method
  asks the client to run.

Running that process is left to the caller. A ``terminal`` method needs the
user's terminal — stdin/stdout attached, foreground process group — which an
embedded host may or may not have, so the SDK never opens one by itself. The
CLI runs it in :mod:`agentao.cli.commands_ext.acp_login`.

Per the ACP v1 authentication spec, a terminal login is the configured agent
program and base launch configuration with the method's ``args`` appended and
its ``env`` applied over the base environment; the descriptor cannot supply a
command, and the client never sends ``authenticate`` for a ``terminal``
method. After the process exits ``0``, the client restarts the agent and
initializes again. Only ``terminal`` methods are supported here; ``agent``
methods (the default when ``type`` is omitted) and others are reported as
unsupported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..capabilities.process import build_child_env
from .errors import AcpRpcError
from .models import AcpServerConfig

#: JSON-RPC error code ACP uses for ``auth_required``.
AUTH_REQUIRED_RPC_CODE = -32000

#: Client capabilities declaring Terminal Auth, in both spellings agents read:
#: the ACP v1 field and the pre-stabilization ``_meta`` extension (which the
#: Registry's auth-check validator still sends, so agents built against it
#: may read only that one).
TERMINAL_AUTH_CLIENT_CAPABILITIES: Dict[str, Any] = {
    "auth": {"terminal": True},
    "_meta": {"terminal-auth": True},
}

#: ``type`` of a method with no ``type`` field, per the spec.
DEFAULT_AUTH_METHOD_TYPE = "agent"
TERMINAL_AUTH_METHOD_TYPE = "terminal"


class AuthMethodError(ValueError):
    """An advertised authentication method that cannot be run as asked."""


def terminal_auth_client_capabilities() -> Dict[str, Any]:
    """A fresh copy of :data:`TERMINAL_AUTH_CLIENT_CAPABILITIES`."""
    return {
        "auth": {"terminal": True},
        "_meta": {"terminal-auth": True},
    }


def normalize_auth_methods(raw: Any) -> List[Dict[str, Any]]:
    """Keep the ``authMethods`` entries that are objects with a string ``id``."""
    if not isinstance(raw, list):
        return []
    return [
        dict(m) for m in raw
        if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]
    ]


def auth_method_type(method: Dict[str, Any]) -> str:
    """The method's ``type``; ``"agent"`` when absent, as the spec defines."""
    kind = method.get("type")
    return kind if isinstance(kind, str) and kind else DEFAULT_AUTH_METHOD_TYPE


def terminal_auth_methods(methods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The ``terminal`` methods among *methods*, in advertised order."""
    return [m for m in methods if auth_method_type(m) == TERMINAL_AUTH_METHOD_TYPE]


def is_auth_required(exc: BaseException) -> bool:
    """Whether *exc* is an agent's ``auth_required`` answer."""
    return isinstance(exc, AcpRpcError) and exc.rpc_code == AUTH_REQUIRED_RPC_CODE


@dataclass(frozen=True)
class TerminalLoginCommand:
    """The interactive process a ``terminal`` auth method asks for."""

    argv: List[str]
    env: Dict[str, str]
    cwd: str
    method_id: str


def build_terminal_login_command(
    config: AcpServerConfig,
    method: Dict[str, Any],
    *,
    base_env: Optional[Dict[str, str]] = None,
) -> TerminalLoginCommand:
    """The login process for *method* on the server configured by *config*.

    The command, base arguments and ``cwd`` are the server's own; the
    method's ``args`` are appended and its ``env`` applied over the server's
    launch environment (``build_child_env(config.env)``, the same scrubbed
    environment the ACP process gets).

    Raises:
        AuthMethodError: *method* is not a ``terminal`` method, or its
            ``args`` / ``env`` are not a list of strings / a string map.
    """
    from .process import resolve_executable

    method_id = method.get("id")
    if not isinstance(method_id, str) or not method_id:
        raise AuthMethodError("authentication method has no id")
    kind = auth_method_type(method)
    if kind != TERMINAL_AUTH_METHOD_TYPE:
        raise AuthMethodError(
            f"authentication method {method_id!r} has type {kind!r}; "
            f"only 'terminal' methods can be run by the client"
        )
    extra_args = method.get("args", [])
    if extra_args is None:
        extra_args = []
    if not isinstance(extra_args, list) or not all(isinstance(a, str) for a in extra_args):
        raise AuthMethodError(
            f"authentication method {method_id!r}: 'args' must be a list of strings"
        )
    extra_env = method.get("env", {})
    if extra_env is None:
        extra_env = {}
    if not isinstance(extra_env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in extra_env.items()
    ):
        raise AuthMethodError(
            f"authentication method {method_id!r}: 'env' must map strings to strings"
        )

    env = build_child_env(config.env, base=base_env)
    env.update(extra_env)
    argv = [
        resolve_executable(config.command, env),
        *config.args,
        *extra_args,
    ]
    return TerminalLoginCommand(argv=argv, env=env, cwd=config.cwd, method_id=method_id)
