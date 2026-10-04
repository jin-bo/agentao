"""``/mcp`` — list / add / remove MCP servers, and log in to or out of OAuth ones."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape

from ...security.terminal_text import sanitize_terminal_text
from .._globals import console, split_subcommand, unknown_subcommand

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def handle_mcp_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /mcp command for MCP server management."""
    from ...mcp.config import _load_json_file, save_mcp_config

    sub, sub_args = split_subcommand(args, default="list", strip_rest=False)

    if sub == "list":
        manager = cli.agent.mcp_manager
        if not manager or not manager.clients:
            console.print("\n[warning]No MCP servers configured.[/warning]")
            console.print("[info]Add servers to .agentao/mcp.json or use /mcp add[/info]\n")
            return

        statuses = manager.get_server_status()
        console.print(f"\n[info]MCP Servers ({len(statuses)}):[/info]\n")
        for s in statuses:
            color = {"connected": "green", "needs_auth": "yellow"}.get(s["status"], "red")
            label = "needs login" if s["status"] == "needs_auth" else s["status"]
            trust_marker = " [dim](trusted)[/dim]" if s["trusted"] else ""
            if s.get("resources") and s.get("resources_enabled", True):
                trust_marker = ", resources" + trust_marker
            elif s.get("resources"):
                trust_marker = ", resources [dim](disabled by config)[/dim]" + trust_marker
            # ``protocol`` and ``error`` are server-authored strings, and every
            # line here is parsed as Rich markup: an unmatched "[/b]" in a
            # third-party error message raises MarkupError out of the command
            # and takes the *whole* listing with it, healthy servers included.
            # Escaping also stops a crafted message from painting itself
            # "[green]connected[/green]".
            protocol = f" [dim]{escape(s['protocol'])}[/dim]" if s.get("protocol") else ""
            console.print(
                f"  [{color}]●[/{color}] [cyan]{escape(s['name'])}[/cyan] "
                f"[dim]{escape(s['transport'])}[/dim]{protocol} — "
                f"[{color}]{label}[/{color}], "
                f"{s['tools']} tool(s){trust_marker}"
            )
            if s.get("skills") is not None:
                skills_line = f"{s['skills']} skill(s)"
                if s.get("skills_error"):
                    skills_line = f"skills unavailable: {s['skills_error']}"
                console.print(f"    [dim]{escape(skills_line)}[/dim]")
            if s["error"]:
                console.print(f"    [red]{escape(s['error'])}[/red]")
        console.print()

    elif sub == "add":
        tokens = sub_args.split() if sub_args else []

        # Optional transport flag for URL servers, accepted either before or
        # after the name (``--http remote <url>`` or ``remote --http <url>``);
        # both orderings are common. Default for a bare URL is Streamable HTTP.
        transport_override = None
        for idx in (0, 1):
            if idx < len(tokens) and tokens[idx] in ("--sse", "--http"):
                transport_override = tokens[idx][2:]  # "sse" | "http"
                tokens = tokens[:idx] + tokens[idx + 1:]
                break

        def _add_usage() -> None:
            console.print("\n[error]Usage: /mcp add [--http|--sse] <name> <command|url> [args...][/error]")
            console.print("[info]Examples:[/info]")
            console.print("  /mcp add github npx -y @modelcontextprotocol/server-github")
            console.print("  /mcp add remote https://api.example.com/mcp        [dim]# Streamable HTTP (default)[/dim]")
            console.print("  /mcp add --sse legacy https://api.example.com/sse  [dim]# legacy SSE[/dim]\n")

        if len(tokens) < 2:
            _add_usage()
            return

        name = tokens[0]
        endpoint = tokens[1]
        extra_args = tokens[2:]

        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            if transport_override:
                # Explicit choice — record it verbatim.
                server_cfg = {"type": transport_override, "url": endpoint}
            else:
                # Default (Streamable HTTP) — write a *bare* url (no type) so the
                # transport stays "inferred". If the endpoint turns out to be a
                # legacy SSE server, the connect-failure hint can then guide the
                # user to add ``--sse`` (an explicit type suppresses that hint).
                server_cfg = {"url": endpoint}
        elif transport_override:
            console.print(
                f"\n[error]--{transport_override} applies to URL servers only; "
                f"'{endpoint}' is not an http(s) URL.[/error]\n"
            )
            return
        else:
            server_cfg = {"command": endpoint}
            if extra_args:
                server_cfg["args"] = extra_args

        project_dir = cli.agent.working_directory / ".agentao"
        project_path = project_dir / "mcp.json"
        existing = _load_json_file(project_path)
        servers = existing.get("mcpServers", {})
        servers[name] = server_cfg
        saved_path = save_mcp_config(servers, config_dir=project_dir)

        console.print(f"\n[success]Added MCP server '{name}' to {saved_path}[/success]")
        console.print("[info]Restart agentao to connect to the new server.[/info]\n")

    elif sub == "remove":
        name = sub_args.strip()
        if not name:
            console.print("\n[error]Usage: /mcp remove <name>[/error]\n")
            return

        project_dir = cli.agent.working_directory / ".agentao"
        project_path = project_dir / "mcp.json"
        existing = _load_json_file(project_path)
        servers = existing.get("mcpServers", {})
        if name not in servers:
            console.print(f"\n[warning]Server '{name}' not found in config.[/warning]\n")
            return

        del servers[name]
        save_mcp_config(servers, config_dir=project_dir)
        _forget_mcp_skill_approvals(cli, name)
        console.print(f"\n[success]Removed MCP server '{name}'.[/success]")
        console.print("[info]Restart agentao to apply changes.[/info]\n")

    elif sub in ("login", "logout"):
        _login_or_logout(cli, sub, sub_args)

    elif sub == "resources":
        _list_resources(cli, sub_args.strip())

    else:
        console.print(unknown_subcommand(sub))
        console.print(
            "[info]Available: /mcp list, /mcp add, /mcp remove, /mcp login, /mcp logout, "
            "/mcp resources[/info]\n"
        )


def _say(text: str) -> None:
    # ``soft_wrap``: an authorization URL must stay one copyable line.
    # Both halves: ``escape`` stops Rich markup, and the sanitizer strips the
    # terminal escapes an authorization server can put in an error description.
    console.print(escape(sanitize_terminal_text(text)), soft_wrap=True)


def _has_registered_tools(cli: AgentaoCLI, server_name: str) -> bool:
    registry = cli.agent.tools
    for tool in registry.list_tools():
        if registry.origin(tool.name) == "mcp" and getattr(tool, "_server_name", None) == server_name:
            return True
    return False


def _login_or_logout(cli: AgentaoCLI, sub: str, sub_args: str) -> None:
    from ..mcp_auth import LoginOutcome, login, logout

    tokens = sub_args.split() if sub_args else []
    open_browser = "--no-browser" not in tokens
    names = [t for t in tokens if t != "--no-browser"]
    usage = "/mcp login <name> [--no-browser]" if sub == "login" else "/mcp logout <name>"
    if len(names) != 1 or (sub == "logout" and not open_browser):
        console.print(f"\n[error]Usage: {usage}[/error]\n")
        return
    name = names[0]
    manager = cli.agent.mcp_manager
    if manager is None:
        console.print("\n[warning]No MCP servers configured.[/warning]\n")
        return
    console.print()
    if sub == "logout":
        if logout(manager, name, write=_say):
            _forget_mcp_skill_approvals(cli, name)
        console.print()
        return
    outcome = login(manager, name, open_browser=open_browser, write=_say)
    if outcome is LoginOutcome.CONNECTED and not _has_registered_tools(cli, name):
        # D8: tools are registered once, at startup; this server had none then.
        console.print(f"[info]Restart agentao to load the tools of '{escape(name)}'.[/info]")
    console.print()


def _forget_mcp_skill_approvals(cli: AgentaoCLI, name: str) -> None:
    """Drop the session's approvals for ``name``'s MCP skills (mcp-skills.md §7).

    After a logout or removal the next activation asks again, and nothing
    read before is served from the cache. Loaded skills stay held: their
    instructions are still in context, so their gates stay on.
    """
    manager = getattr(getattr(cli, "agent", None), "skill_manager", None)
    mcp_skills = getattr(manager, "mcp_skills", None)
    if mcp_skills is not None:
        mcp_skills.forget_server(name)


def _list_resources(cli: AgentaoCLI, server: str) -> None:
    """``/mcp resources [server]`` — through the manager, no model turn."""
    from ...mcp.resources import (
        McpResourceError,
        format_size,
        list_everywhere,
        visible_resources,
        visible_templates,
        walk_all_pages,
    )

    manager = cli.agent.mcp_manager
    if manager is None or not manager.server_configs:
        console.print("\n[warning]No MCP servers configured.[/warning]\n")
        return

    def clean(text) -> str:
        return escape(sanitize_terminal_text(str(text)))

    resources, templates, errors = [], [], []
    if server:
        if server not in manager.server_configs:
            console.print(f"\n[error]MCP server '{clean(server)}' not found.[/error]\n")
            return
        if not manager.resources_allowed(server):
            console.print(
                f"\n[warning]Resources are disabled by config for '{clean(server)}' "
                '("resources": false in mcp.json).[/warning]\n'
            )
            return
        try:
            def resource_page(cursor):
                page = manager.list_resources(server, cursor)
                return page.resources, page.next_cursor

            def template_page(cursor):
                page = manager.list_resource_templates(server, cursor)
                return page.templates, page.next_cursor

            resources = walk_all_pages(server, resource_page, "resources")
            templates = walk_all_pages(server, template_page, "resource templates")
        except McpResourceError as e:
            if e.kind == "unsupported":
                console.print(
                    f"\n[info]MCP server '{clean(server)}' declares no resources.[/info]\n"
                )
            else:
                console.print(f"\n[error]{clean(e)}[/error]\n")
            return
    else:
        disabled = sorted(n for n in manager.server_configs if not manager.resources_allowed(n))
        resources, errors = list_everywhere(manager, templates=False)
        templates, template_errors = list_everywhere(manager, templates=True)
        reported = {e["server"] for e in errors}
        errors += [e for e in template_errors if e["server"] not in reported]
        for name in disabled:
            console.print(f"[dim]  {clean(name)}: resources disabled by config[/dim]")

    resources = visible_resources(resources)
    templates = visible_templates(templates)
    if not resources and not templates and not errors:
        where = f"'{clean(server)}'" if server else "any connected server"
        console.print(f"\n[info]No MCP resources from {where}.[/info]\n")
        return

    console.print(f"\n[info]MCP resources ({len(resources)}):[/info]")
    for r in resources:
        details = ", ".join(
            x for x in (r.mime_type, format_size(r.size) if isinstance(r.size, int) else None) if x
        )
        suffix = f" [dim]({clean(details)})[/dim]" if details else ""
        console.print(
            f"  [cyan]{clean(r.server)}[/cyan] {clean(r.uri)} — {clean(r.title or r.name)}{suffix}"
        )
    if templates:
        console.print(f"\n[info]Resource templates ({len(templates)}):[/info]")
        for t in templates:
            suffix = f" [dim]({clean(t.mime_type)})[/dim]" if t.mime_type else ""
            console.print(
                f"  [cyan]{clean(t.server)}[/cyan] {clean(t.uri_template)} — "
                f"{clean(t.title or t.name)}{suffix}"
            )
    for e in errors:
        console.print(f"  [red]{clean(e['server'])}: {clean(e['error'])}[/red]")
    console.print()
