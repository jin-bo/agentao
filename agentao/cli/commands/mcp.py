"""``/mcp`` — list / add / remove MCP servers in the project config."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
            color = "green" if s["status"] == "connected" else "red"
            trust_marker = " [dim](trusted)[/dim]" if s["trusted"] else ""
            console.print(
                f"  [{color}]●[/{color}] [cyan]{s['name']}[/cyan] "
                f"[dim]{s['transport']}[/dim] — "
                f"[{color}]{s['status']}[/{color}], "
                f"{s['tools']} tool(s){trust_marker}"
            )
            if s["error"]:
                console.print(f"    [red]{s['error']}[/red]")
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
        console.print(f"\n[success]Removed MCP server '{name}'.[/success]")
        console.print("[info]Restart agentao to apply changes.[/info]\n")

    else:
        console.print(unknown_subcommand(sub))
        console.print("[info]Available: /mcp list, /mcp add, /mcp remove[/info]\n")
