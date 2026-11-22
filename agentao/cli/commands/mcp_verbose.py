"""``/mcp verbose [server]`` — the slow diagnostic view of each MCP server.

Bare ``/mcp`` stays a one-line-per-server summary. This view adds, per
server: how it is started (command or URL), the names of its env vars and
headers, how it signs in, which of its tools are registered and which a tool
filter hid, its resources and templates, and its skills.

**What is hidden.** This is a list of rules, not a guarantee: a secret in
a spot no rule covers (a bare token as a positional arg) is still printed.

- Env vars and headers show their names only, as ``KEY=*****``.
- A server URL shows only ``scheme://host[:port]``. Its path, query,
  fragment and userinfo are hidden: Zapier-style URLs carry a key in the
  path.
- ``load_mcp_config`` expands ``${VAR}`` in ``args``, so the live config can
  hold a key that the file only names. The args come from the raw
  ``mcp.json`` entry instead, and only when that entry, expanded, is exactly
  what the client runs. Otherwise (a server a host or ACP passed in, a file
  edited since startup) only the program name is shown.
- In the args shown, :func:`mask_args` hides the value of every
  ``KEY=VALUE`` arg, the arg after a flag whose name says it holds a secret
  (``--token``, ``--api-key``, ``--header``, …), the word after ``Bearer`` or
  ``Basic``, and redacts any ``scheme://`` arg (``https://``, a database
  DSN) like a server URL. A value
  that is only a ``${VAR}`` reference stays: it is a name, not a value.

Every string a server wrote goes through ``sanitize_terminal_text`` and
``rich.markup.escape``.

Resources are the only network requests here: one listing per server that
declares them, the same requests ``/mcp resources`` makes. A failure on one
server is reported on its own line and does not stop the others.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from rich.markup import escape

from ...security.terminal_text import sanitize_terminal_text
from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI

#: Resources or templates shown per server before "… N more". Tools and
#: skills are listed whole: a diagnostic view that hides a callable tool's
#: name is no diagnostic.
MAX_LISTED = 10

MASK = "*****"

#: A flag whose next arg is a secret: ``--token X``, ``--api-key X``, ``-H X``.
_SECRET_FLAG_RE = re.compile(
    r"^-{1,2}(?:[\w-]*(?:token|key|secret|passw(?:or)?d|auth|credential|bearer|"
    r"cookie|session|header)[\w-]*|(?-i:H))$",
    re.IGNORECASE,
)
#: A value that only names a variable, which the file shows anyway.
_VAR_ONLY_RE = re.compile(r"^\$(?:\{\w+\}|\w+)$")
#: Any ``scheme://`` arg: ``https://…`` and a DSN such as
#: ``postgresql://user:pass@host/db`` alike, redacted like a server URL.
_URL_ARG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
#: ``Bearer <token>`` / ``Basic <token>`` inside an arg.
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(bearer|basic)(\s+)(?!\$(?:\{\w+\}|\w+)(?:\s|$))\S+")


def _clean(text: Any) -> str:
    return escape(" ".join(sanitize_terminal_text(str(text)).split()))


def _clean_keep_spaces(text: Any) -> str:
    """Like :func:`_clean`, but keeps runs of spaces: they matter inside quotes."""
    text = sanitize_terminal_text(str(text)).replace("\n", "\\n").replace("\t", "\\t")
    return escape(text)


def redact_url(url: str) -> str:
    """``scheme://host[:port]`` of ``url``, saying which other parts were hidden.

    The path is hidden too, not only userinfo, query and fragment: some
    services put the key in the path (``/mcp/sk-…/sse``).
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "(unparsable URL)"
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port else host
    redacted = urlunsplit((parts.scheme, netloc, "", "", ""))
    hidden = []
    if parts.path not in ("", "/"):
        redacted += "/…"
        hidden.append("path")
    if parts.query:
        hidden.append("query")
    if parts.fragment:
        hidden.append("fragment")
    if parts.username or parts.password:
        hidden.append("credentials")
    if hidden:
        redacted += f" ({', '.join(hidden)} hidden)"
    return redacted


def mask_args(args: List[str]) -> List[str]:
    """``args`` with the values that look like secrets replaced by ``*****``.

    A heuristic: see the module docstring for what it covers and what it
    does not.
    """
    out: List[str] = []
    mask_next = False
    for arg in args:
        if mask_next:
            mask_next = False
            out.append(arg if _VAR_ONLY_RE.match(arg) else MASK)
            continue
        if _URL_ARG_RE.match(arg):
            out.append(redact_url(arg))
            continue
        if _AUTH_SCHEME_RE.search(arg):
            # Before the ``=`` split: a base64 credential ends in ``=``
            # padding, and the split would show it as the key.
            out.append(_AUTH_SCHEME_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", arg))
            continue
        if "=" in arg:
            key, value = arg.split("=", 1)
            out.append(arg if _VAR_ONLY_RE.match(value) else f"{key}={MASK}")
            continue
        if _SECRET_FLAG_RE.match(arg):
            mask_next = True
        out.append(arg)
    return out


def _masked_names(mapping: Any) -> str:
    if not isinstance(mapping, dict) or not mapping:
        return ""
    return ", ".join(f"{k}=*****" for k in sorted(str(k) for k in mapping))


def _raw_entries(cli: AgentaoCLI) -> Dict[str, Dict[str, Any]]:
    """Unexpanded ``mcpServers`` entries, user scope winning like the loader.

    Read quietly: startup already warned about a broken file, and this runs
    on every ``/mcp verbose``.
    """
    from ...mcp.config import _read_json_object
    from ...paths import user_root

    entries: Dict[str, Dict[str, Any]] = {}
    paths: List[Path] = []
    try:
        paths.append(user_root() / "mcp.json")
    except Exception:
        pass
    paths.append(Path(cli.agent.working_directory) / ".agentao" / "mcp.json")
    for path in paths:
        try:
            servers = _read_json_object(path)[0].get("mcpServers")
        except Exception:
            continue
        if not isinstance(servers, dict):
            continue
        for name, cfg in servers.items():
            if isinstance(cfg, dict) and name not in entries:
                entries[name] = cfg
    return entries


def command_display(live: Dict[str, Any], raw: Optional[Dict[str, Any]]) -> str:
    """The command line to show, with the file's ``${VAR}`` spellings kept.

    The raw args are shown only when they expand to exactly the live args,
    so a value that came from the environment is never printed, and then
    through :func:`mask_args` and quoted with ``shlex.join``. Not markup.
    """
    from ...mcp.config import expand_env_vars

    command = str(live.get("command", ""))
    live_args = live.get("args") or []
    if not live_args:
        return command
    if raw is not None:
        raw_args = raw.get("args")
        raw_command = raw.get("command")
        if (
            isinstance(raw_args, list)
            and all(isinstance(a, str) for a in raw_args)
            and raw_command == live.get("command")
            and [expand_env_vars(a) for a in raw_args] == list(live_args)
        ):
            return shlex.join([command, *mask_args(raw_args)])
    return f"{command} (args hidden: not read from mcp.json)"


def _auth_line(client: Any) -> str:
    from ...mcp.config import resolve_oauth

    config = client.config
    if client.transport_type == "stdio":
        return "n/a (stdio)"
    try:
        oauth = resolve_oauth(config)
    except Exception as exc:
        return f"invalid oauth config: {_clean(exc)}"
    if oauth is None:
        headers = config.get("headers") or {}
        if isinstance(headers, dict) and any(
            isinstance(k, str) and k.lower() == "authorization" for k in headers
        ):
            return "Authorization header"
        return "off (\"oauth\": false)"
    name = _clean(client.name)
    if getattr(client.status, "value", client.status) == "needs_auth":
        return f"OAuth: login required — run /mcp login {name}"
    try:
        profile = oauth.get("profile")
        record = client.oauth_runtime.store.load(config.get("url", ""), profile)
    except Exception:
        record = None
    if record is None:
        return f"OAuth: no credentials stored (/mcp login {name} if the server asks)"
    # A stored record says nothing about whether the token still works; the
    # summary line above says whether the connection does.
    return "OAuth: credentials stored" + (f" (profile {_clean(profile)})" if profile else "")


def _registered_tool_names(cli: AgentaoCLI, server: str) -> set:
    registry = cli.agent.tools
    names = set()
    for tool in registry.list_tools():
        if registry.origin(tool.name) != "mcp":
            continue
        if getattr(tool, "_server_name", None) != server:
            continue
        mcp_tool = getattr(tool, "_mcp_tool", None)
        if mcp_tool is not None:
            names.add(mcp_tool.name)
    return names


def _listing(label: str, items: List[str], cap: Optional[int] = None) -> str:
    if not items:
        return f"    • {label}: (none)"
    shown = ", ".join(items if cap is None else items[:cap])
    more = 0 if cap is None else len(items) - cap
    suffix = f", … {more} more" if more > 0 else ""
    return f"    • {label}: {shown}{suffix}"


def _resource_lines(manager: Any, client: Any) -> List[str]:
    from ...mcp.resources import (
        McpResourceError,
        visible_resources,
        visible_templates,
        walk_all_pages,
    )

    name = client.name
    if not manager.resources_allowed(name):
        return ["    • Resources: [dim]disabled by config[/dim]"]
    if getattr(client.status, "value", client.status) != "connected":
        # A listing would try to reconnect (or hit a login wall); the summary
        # line above already says why the server is down.
        return ["    • Resources: (not connected)"]
    if not client.supports_resources:
        return ["    • Resources: (not declared)"]

    def resource_page(cursor):
        page = manager.list_resources(name, cursor)
        return page.resources, page.next_cursor

    def template_page(cursor):
        page = manager.list_resource_templates(name, cursor)
        return page.templates, page.next_cursor

    lines = []
    try:
        resources = visible_resources(walk_all_pages(name, resource_page, "resources"))
        lines.append(_listing("Resources", [
            f"{_clean(r.title or r.name)} [dim]({_clean(r.uri)})[/dim]" for r in resources
        ], cap=MAX_LISTED))
    except Exception as exc:
        return [f"    • Resources: [red]{_clean(exc)}[/red]"]
    try:
        templates = visible_templates(walk_all_pages(name, template_page, "resource templates"))
        lines.append(_listing("Resource templates", [
            f"{_clean(t.title or t.name)} [dim]({_clean(t.uri_template)})[/dim]"
            for t in templates
        ], cap=MAX_LISTED))
    except McpResourceError as exc:
        if exc.kind != "unsupported":
            lines.append(f"    • Resource templates: [red]{_clean(exc)}[/red]")
    except Exception as exc:
        lines.append(f"    • Resource templates: [red]{_clean(exc)}[/red]")
    return lines


def server_detail_lines(cli: AgentaoCLI, client: Any, raw: Optional[Dict[str, Any]]) -> List[str]:
    """The detail lines under one server, as Rich markup."""
    config = client.config if isinstance(client.config, dict) else {}
    lines: List[str] = []
    if client.transport_type == "stdio":
        lines.append(f"    • Command: {_clean_keep_spaces(command_display(config, raw))}")
        if config.get("cwd"):
            lines.append(f"    • Cwd: {_clean(config['cwd'])}")
        env = _masked_names(config.get("env"))
        if env:
            lines.append(f"    • Env: {_clean(env)}")
    else:
        lines.append(f"    • URL: {_clean(redact_url(str(config.get('url', ''))))}")
        headers = _masked_names(config.get("headers"))
        if headers:
            lines.append(f"    • Headers: {_clean(headers)}")
    lines.append(f"    • Auth: {_auth_line(client)}")

    offered = sorted({t.name for t in client.tools})
    registered = _registered_tool_names(cli, client.name)
    lines.append(_listing("Tools", [_clean(n) for n in offered if n in registered]))
    hidden = [n for n in offered if n not in registered]
    if hidden and getattr(client.status, "value", client.status) == "connected":
        lines.append(
            f"    • [dim]Not offered to the model (tool filter or name clash): "
            f"{', '.join(_clean(n) for n in hidden)}[/dim]"
        )

    lines.extend(_resource_lines(cli.agent.mcp_manager, client))

    if client.skills_requested:
        lines.extend(_skill_lines(cli, client))
    return lines


def _skill_lines(cli: AgentaoCLI, client: Any) -> List[str]:
    """Each skill's name, with ``(active)`` / ``(disabled)`` from this session."""
    from ...mcp.skills import model_name

    entries = list(client.skill_entries)
    if not entries and client.skills_problem:
        return [f"    • Skills: [red]{_clean(client.skills_problem)}[/red]"]
    manager = getattr(cli.agent, "skill_manager", None)
    # ``or``: a host-injected manager may carry ``None`` here.
    active = getattr(manager, "active_skills", None) or {}
    disabled = getattr(manager, "disabled_skills", None) or ()
    names = []
    for e in entries:
        label = _clean(e.name or e.uri)
        full = model_name(e.label, e.uri)
        tags = [t for t, on in (("active", full in active), ("disabled", full in disabled)) if on]
        if tags:
            label += f" [dim]({', '.join(tags)})[/dim]"
        if e.unavailable:
            label += f" [dim](unavailable: {_clean(e.unavailable)})[/dim]"
        names.append(label)
    return [_listing("Skills", names)]


def show_verbose(cli: AgentaoCLI, server: str = "") -> None:
    """Print each server's summary line, then its detail lines."""
    from .mcp import print_server_summary

    manager = cli.agent.mcp_manager
    if not manager or not manager.clients:
        console.print("\n[warning]No MCP servers configured.[/warning]")
        console.print("[info]Add servers to .agentao/mcp.json or use /mcp add[/info]\n")
        return
    clients = dict(manager.clients)
    if server and server not in clients:
        console.print(f"\n[error]MCP server '{_clean(server)}' not found.[/error]\n")
        return

    raw = _raw_entries(cli)
    statuses = {s["name"]: s for s in manager.get_server_status()}
    names = [server] if server else sorted(clients)
    console.print(f"\n[info]MCP Servers ({len(names)}):[/info]\n")
    for name in names:
        status = statuses.get(name)
        if status is not None:
            print_server_summary(status)
        else:
            console.print(f"  [cyan]{_clean(name)}[/cyan]")
        try:
            detail = server_detail_lines(cli, clients[name], raw.get(name))
        except Exception as exc:  # one broken server must not hide the rest
            detail = [f"    • [red]details unavailable: {_clean(exc)}[/red]"]
        for line in detail:
            console.print(line, soft_wrap=True)
        console.print()
