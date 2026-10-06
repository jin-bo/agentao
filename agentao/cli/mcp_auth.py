"""MCP OAuth login and logout, shared by ``/mcp login`` and ``agentao mcp login``.

docs/design/mcp-oauth.md §7, §8. Both surfaces run the same two functions
over a :class:`~agentao.mcp.McpClientManager`; they differ only in where the
lines go (Rich in the REPL, plain stderr from a shell) and in what the user
is told about tools afterwards (D8).

Standard library only, like the UI it drives: ``agentao mcp login`` is how an
ACP user logs in, from a bare ``pip install agentao``.
"""

from __future__ import annotations

import argparse
import sys
from enum import Enum
from typing import Any, Callable, List, Optional

from ..security.terminal_text import sanitize_terminal_text

Writer = Callable[[str], None]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130

#: How long a cancelled login may take to close its listener and paste prompt.
_CANCEL_CLEANUP_S = 3.0


class LoginOutcome(Enum):
    CONNECTED = "connected"
    STORED = "stored"  # the credential was saved, the reconnect did not succeed
    FAILED = "failed"
    CANCELLED = "cancelled"


def _settings(manager: Any, name: str, write: Writer) -> Optional[dict]:
    from ..mcp.config import McpOAuthConfigError, McpTransportConfigError, resolve_oauth

    config = manager.server_configs.get(name)
    if config is None:
        known = ", ".join(sorted(manager.server_configs)) or "none"
        write(f"MCP server '{name}' is not configured (configured: {known}).")
        return None
    try:
        settings = resolve_oauth(config)
    except (McpOAuthConfigError, McpTransportConfigError) as e:
        write(f"MCP server '{name}': {e}")
        return None
    if settings is None:
        write(
            f"MCP server '{name}' does not use OAuth (a stdio server, an "
            "'Authorization' header, or \"oauth\": false)."
        )
        return None
    return settings


def login(
    manager: Any,
    name: str,
    *,
    open_browser: bool = True,
    write: Writer,
    ui_factory: Optional[Callable[..., Any]] = None,
) -> LoginOutcome:
    """Run one server's login through the CLI UI, then report its status."""
    from ..mcp.config import McpEnvVarError, check_credential_vars
    from ..mcp.oauth import OAuthLoginError
    from .mcp_login_ui import CliLoginUI

    # Before ``_settings``: an unset ``oauth.client_secret`` variable expands to
    # ``""``, which ``resolve_oauth`` refuses as "must be a non-empty string"
    # without naming the variable. Login only — logout must still work.
    config = manager.server_configs.get(name)
    if isinstance(config, dict):
        try:
            check_credential_vars(config)
        except McpEnvVarError as e:
            write(f"MCP server '{name}': {e}")
            return LoginOutcome.FAILED
    settings = _settings(manager, name, write)
    if settings is None:
        return LoginOutcome.FAILED
    ui = (ui_factory or CliLoginUI)(
        name,
        redirect_host=settings.get("redirect_host"),
        callback_port=settings.get("callback_port"),
        open_browser=open_browser,
        write=write,
    )
    try:
        status = manager.login(name, ui)
    except KeyboardInterrupt:
        # The cancel reaches the login on the MCP loop asynchronously; wait
        # (bounded) for its UI to close, so a paste prompt still holding the
        # terminal cannot read or reset the REPL's next prompt.
        closed = getattr(ui, "closed", None)
        if (
            closed is not None
            and getattr(ui, "in_use", False)
            and not closed.wait(_CANCEL_CLEANUP_S)
        ):
            write(f"Login to '{name}': the login screen did not close in time.")
        write(f"Login to '{name}' cancelled.")
        return LoginOutcome.CANCELLED
    except (OAuthLoginError, TimeoutError) as e:
        write(f"Login to '{name}' failed: {e}")
        return LoginOutcome.FAILED
    except Exception as e:  # an SDK or network error, reported, not raised
        write(f"Login to '{name}' failed: {type(e).__name__}: {e}")
        return LoginOutcome.FAILED
    client = manager.get_client(name)
    if getattr(status, "value", status) == "connected":
        tools = len(client.tools) if client is not None else 0
        write(f"Logged in to '{name}' — connected, {tools} tool(s).")
        return LoginOutcome.CONNECTED
    error = getattr(client, "error_message", None) if client is not None else None
    write(
        f"Logged in to '{name}' and stored the credential, but the reconnect ended "
        f"'{getattr(status, 'value', status)}'" + (f": {error}" if error else ".")
    )
    return LoginOutcome.STORED


def logout(manager: Any, name: str, *, write: Writer) -> bool:
    """Delete one server's stored credential and disconnect it."""
    if _settings(manager, name, write) is None:
        return False
    try:
        deleted = manager.logout(name)
    except Exception as e:
        write(f"Logout from '{name}' failed: {type(e).__name__}: {e}")
        return False
    if deleted:
        write(f"Logged out of '{name}': its stored credential was deleted.")
    else:
        write(f"'{name}' had no stored credential.")
    return True


# -- ``agentao mcp login|logout <name>`` -----------------------------------


def add_mcp_subparser(subparsers: Any) -> None:
    mcp_parser = subparsers.add_parser(
        "mcp", help="Log in to or out of an OAuth MCP server."
    )
    mcp_parser.add_argument(
        "--plugin-dir", dest="sub_plugin_dirs", action="append", default=None,
        metavar="DIR", help="Load a plugin from DIR (repeatable), for its MCP servers.",
    )
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")
    login_p = mcp_sub.add_parser(
        "login", help="Authorize an MCP server in the browser and store the credential."
    )
    login_p.add_argument("name", help="Server name from mcp.json")
    login_p.add_argument(
        "--no-browser",
        dest="no_browser",
        action="store_true",
        default=False,
        help="Print the URL and accept the pasted redirect instead of opening a browser.",
    )
    logout_p = mcp_sub.add_parser("logout", help="Delete an MCP server's stored credential.")
    logout_p.add_argument("name", help="Server name from mcp.json")


def _plain(text: str) -> None:
    sys.stderr.write(sanitize_terminal_text(text) + "\n")
    sys.stderr.flush()


def plugin_mcp_servers(base: dict, plugin_dirs: List[Any]) -> dict:
    """The MCP servers plugins add, as the REPL's ``_load_and_register_plugins`` finds them.

    Installed plugins plus ``--plugin-dir`` ones, minus any whose skills or
    agents fail to resolve — the REPL drops those before merging their MCP
    servers. (A registration conflict with an agent's existing skills cannot
    be seen without an agent; logging in to such a server only stores a
    credential nothing uses.)
    """
    from ..embedding.plugins.manager import PluginManager
    from ..embedding.plugins.mcp import merge_plugin_mcp_servers
    from ..embedding.plugins.resolvers.agents import resolve_plugin_agents
    from ..embedding.plugins.resolvers.skills import resolve_plugin_entries

    loaded = PluginManager(inline_dirs=list(plugin_dirs) or None).load_plugins()
    usable = [
        p for p in loaded
        if not resolve_plugin_entries(p)[2] and not resolve_plugin_agents(p)[2]
    ]
    if not usable:
        return {}
    merged = merge_plugin_mcp_servers(base, usable).servers
    return {name: cfg for name, cfg in merged.items() if name not in base}


def _manager_from_config(plugin_dirs: List[Any]) -> Any:
    from pathlib import Path

    from .._env import safe_load_dotenv
    from ..mcp import McpClientManager, load_mcp_config
    from ..paths import user_root

    # As the REPL does: ``$VAR`` in a server's config may live in ``.env``.
    safe_load_dotenv()
    configs = load_mcp_config(project_root=Path.cwd().resolve(), user_root=user_root())
    try:
        configs.update(plugin_mcp_servers(configs, plugin_dirs))
    except Exception as e:  # a broken plugin must not block logging in to mcp.json servers
        _plain(f"warning: plugin MCP servers were not loaded: {e}")
    # Nothing is connected up front: the login reconnects only its server.
    return McpClientManager(configs)


def handle_mcp_subcommand(
    args: argparse.Namespace,
    *,
    write: Writer = _plain,
    manager_factory: Callable[[List[Any]], Any] = _manager_from_config,
) -> int:
    """``agentao mcp login|logout <name>``; returns the exit status."""
    action = getattr(args, "mcp_action", None)
    if action not in ("login", "logout"):
        write("usage: agentao mcp {login,logout} <name> [--no-browser]")
        return EXIT_USAGE
    from pathlib import Path

    plugin_dirs = [
        Path(d)
        for d in (getattr(args, "plugin_dirs", None) or []) + (getattr(args, "sub_plugin_dirs", None) or [])
    ]
    manager = manager_factory(plugin_dirs)
    try:
        if action == "logout":
            return EXIT_OK if logout(manager, args.name, write=write) else EXIT_FAILED
        outcome = login(manager, args.name, open_browser=not args.no_browser, write=write)
        if outcome is LoginOutcome.CANCELLED:
            return EXIT_CANCELLED
        if outcome is not LoginOutcome.CONNECTED:
            return EXIT_FAILED
        write(
            "An agentao session that already has this server's tools uses the new "
            "credential on its next call; one started while the server needed login "
            "has to be restarted to load them."
        )
        return EXIT_OK
    finally:
        manager.disconnect_all()


def needs_login_lines(statuses: List[dict]) -> List[str]:
    """One startup line per server waiting for a login (§8.1)."""
    return [
        f"MCP server '{s['name']}' needs login — run /mcp login {s['name']}"
        for s in statuses
        if s.get("status") == "needs_auth"
    ]
