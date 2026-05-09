"""``/mcp`` — list / add / remove MCP servers in the project config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def handle_mcp_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /mcp command for MCP server management."""
    from ...mcp.config import _load_json_file, save_mcp_config

    args = args.strip()
    parts = args.split(None, 1) if args else []
    sub = parts[0] if parts else "list"
    sub_args = parts[1] if len(parts) > 1 else ""

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
        add_parts = sub_args.split(None, 1) if sub_args else []
        if len(add_parts) < 2:
            console.print("\n[error]Usage: /mcp add <name> <command|url> [args...][/error]")
            console.print("[info]Examples:[/info]")
            console.print("  /mcp add github npx -y @modelcontextprotocol/server-github")
            console.print("  /mcp add remote https://api.example.com/sse\n")
            return

        name = add_parts[0]
        endpoint = add_parts[1]

        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            server_cfg = {"url": endpoint}
        else:
            cmd_parts = endpoint.split()
            server_cfg = {"command": cmd_parts[0]}
            if len(cmd_parts) > 1:
                server_cfg["args"] = cmd_parts[1:]

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
        console.print(f"\n[error]Unknown subcommand: {sub}[/error]")
        console.print("[info]Available: /mcp list, /mcp add, /mcp remove[/info]\n")
