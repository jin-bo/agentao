"""CLI display helpers: welcome banner, /help text, /skills, /status.

Split out from ``app.py`` to keep the class slim. All functions take
the ``AgentaoCLI`` instance as their first argument.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown

from ._globals import console

if TYPE_CHECKING:
    from .app import AgentaoCLI


def print_welcome(cli: "AgentaoCLI") -> None:
    current_model = cli.agent.get_current_model()

    logo = [
        "   ___                      _                ",
        "  / _ \\ ___ _ ___  ___  ___| |_  ___  ___  ",
        " /  _  // _` / -_)| _ \\/ _ \\  _|/ _` / _ \\ ",
        "/_/ |_| \\__, \\___||_// \\___/\\__|\\__,_\\___/ ",
    ]

    console.print()
    for line in logo:
        console.print(f"[bold cyan]{line}[/bold cyan]")
    console.print("[bold cyan]        |___/        [/bold cyan][bold yellow](The Way of Agents)[/bold yellow]")
    console.print()
    console.print(f"  [dim]Model:[/dim] [green]{current_model}[/green]  [dim]|[/dim]  [dim]Type[/dim] [cyan]/help[/cyan] [dim]for commands[/dim]")
    console.print()


def print_help(cli: "AgentaoCLI") -> None:
    help_text = """
# Agentao Help

**Available Commands:**
All commands start with `/`:

- `/help` - Show this help message
- `/model` - List available models or switch model
  - `/model` - Show current model and available models
  - `/model <name>` - Switch to specified model
- `/provider` - List or switch API providers
  - `/provider` - Show current provider and available providers
  - `/provider <NAME>` - Switch to provider (reads XXXX_API_KEY, XXXX_BASE_URL, XXXX_MODEL from env)
- `/clear` - End current session (saves it) and start a new one
  - Clears conversation history, all memories, and resets permission mode to workspace-write
  - `/clear all` - Alias for `/clear` (backward compatible)
- `/new` - Alias for `/clear`; start a fresh session
- `/status` - Show conversation status
- `/temperature [value]` - Show or set LLM temperature (0.0-2.0)
- `/mode [read-only|workspace-write|full-access]` - Set permission mode
  - `/mode` - Show current mode
  - `/mode read-only` - Block all write & shell tools
  - `/mode workspace-write` - Allow file writes & safe shell; ask for web (default)
  - `/mode full-access` - Allow all tools without prompting
- `/plan` - Plan mode workflow (read-only; LLM plans, not executes)
  - `/plan` - Enter plan mode; if already on, shows current saved plan
  - `/plan show` - Display the saved plan file
  - `/plan implement` - Exit plan mode, restore prior permissions, show plan
  - `/plan clear` - Archive and clear the current plan
  - `/plan history` - List recent archived plans
- `/skills` - List available skills
- `/crystallize [subcommand]` - Draft a reusable skill from the current session
  - `/crystallize` or `/crystallize suggest` - Analyze the session and generate a skill draft
  - `/crystallize feedback <text>` - Add feedback and rewrite the current draft
  - `/crystallize revise` - Interactively enter feedback and rewrite the draft
  - `/crystallize refine` - Improve the current draft with skill-creator guidance
  - `/crystallize status` - Show current pending draft status
  - `/crystallize clear` - Clear the current pending draft
  - `/crystallize create [name]` - Save the draft into skills/ and reload
  - Recommended flow: `suggest` → `feedback <text>` (repeatable) → `refine` → `create [name]`
- `/memory [subcommand] [arg]` - Manage saved memories
  - `/memory` or `/memory list` - Show all saved memories (with tag summary)
  - `/memory search <query>` - Search memories by keyword (key, value, tags)
  - `/memory tag <tag>` - Filter memories by tag
  - `/memory delete <key>` - Delete a specific memory
  - `/memory clear` - Clear all memories (requires confirmation)
- `/context` - Show context window token usage and limit
  - `/context limit <n>` - Set max context tokens (default: 200,000)
- `/plugins` - List loaded plugins with diagnostics
- `/mcp [subcommand]` - Manage MCP servers
  - `/mcp` or `/mcp list` - List MCP servers with status and tools
  - `/mcp add <name> <command|url>` - Add an MCP server
  - `/mcp remove <name>` - Remove an MCP server
- `/sandbox [subcommand]` - Control macOS sandbox-exec for shell commands (macOS only)
  - `/sandbox` or `/sandbox status` - Show current sandbox state
  - `/sandbox on` / `/sandbox off` - Toggle for this session
  - `/sandbox profile <name>` - Switch to a built-in or user profile
  - `/sandbox profiles` - List available profiles
- `/acp [subcommand]` - Manage ACP servers
  - `/acp` or `/acp list` - List ACP servers with state
  - `/acp start/stop/restart <name>` - Control server lifecycle
  - `/acp send <name> <message>` - Send a prompt (permission/input handled inline)
  - `/acp cancel <name>` - Cancel active turn
  - `/acp status <name>` - Detailed server status
  - `/acp logs <name> [lines]` - View server stderr
- `/replay [on|off]` - Toggle persistent replay recording (writes `.agentao/settings.json`)
- `/replays` - List, inspect, or prune recorded replay instances
  - `/replays` or `/replays list` - List replay instances
  - `/replays show <id>` - Render events in sequence order
  - `/replays tail <id> [n]` - Show last n events (default 20)
  - `/replays prune` - Delete replays beyond `replay.max_instances`
- `/copy` - Copy the last assistant response (Markdown) to clipboard
- `/markdown` - Toggle Markdown rendering ON/OFF (default: ON)
- `/exit` or `/quit` - Exit the program

**Available Tools:**
The agent has access to the following tools:
- `read_file` - Read file contents with line numbers (supports offset/limit)
- `write_file` - Write/append content to files
- `replace` - Edit files by replacing text (supports replace_all)
- `list_directory` - List directory contents
- `glob` - Find files matching patterns
- `search_file_content` - Search text in files
- `run_shell_command` - Execute shell commands
- `web_fetch` - Fetch web content
- `web_search` - Search the web
- `activate_skill` - Activate skills
- `cli_help` - Get CLI help
- `codebase_investigator` - Investigate codebases

**Skills:**
Type `/skills` to see available skills, or ask the agent to activate a specific skill.

**Examples:**
- "Read the file main.py"
- "Search for function definitions in Python files"
- "Fetch content from https://example.com"
- "Activate the pdf skill to help me work with PDF files"

**Note:** Regular messages (without `/`) are sent to the AI agent.
"""
    console.print(Markdown(help_text))


def list_skills(cli: "AgentaoCLI") -> None:
    sm = cli.agent.skill_manager
    available = sm.list_available_skills()
    disabled = sorted(sm.disabled_skills & set(sm.available_skills.keys()))

    console.print(f"\n[info]Available Skills ({len(available)}):[/info]\n")
    for skill_name in sorted(available):
        skill_info = sm.get_skill_info(skill_name)
        title = skill_info.get('title', skill_name) if skill_info else skill_name
        desc = skill_info.get('description', 'No description')[:100] if skill_info else 'No description'
        console.print(f"  • [cyan]{skill_name}[/cyan] - {title}")
        if desc:
            console.print(f"    {desc}...")

    if disabled:
        console.print(f"\n[info]Disabled Skills ({len(disabled)}):[/info]\n")
        for skill_name in disabled:
            skill_info = sm.get_skill_info(skill_name)
            title = skill_info.get('title', skill_name) if skill_info else skill_name
            console.print(f"  • [dim]{skill_name}[/dim] - {title}")

    console.print("\n[info]Active Skills:[/info]")
    active = sm.get_active_skills()
    if active:
        for skill, info in active.items():
            console.print(f"  • [success]{skill}[/success]: {info['task']}")
    else:
        console.print("  None")
    console.print()


def show_status(cli: "AgentaoCLI") -> None:
    summary = cli.agent.get_conversation_summary()
    console.print(f"\n[info]Status:[/info]\n{summary}")

    from ..permissions import PermissionMode
    _mode_labels = {
        PermissionMode.READ_ONLY:       ("[red]read-only[/red]",       "write & shell tools are blocked"),
        PermissionMode.WORKSPACE_WRITE: ("[green]workspace-write[/green]", "file writes & safe shell allowed, web asks"),
        PermissionMode.FULL_ACCESS:     ("[yellow]full-access[/yellow]",   "all tools allowed without prompting"),
    }
    _label, _desc = _mode_labels.get(cli.current_mode, (cli.current_mode.value, ""))
    console.print(f"[info]Permission Mode:[/info] {_label}  [dim]({_desc})[/dim]")

    md_state = "[green]ON[/green]" if cli.markdown_mode else "[yellow]OFF[/yellow]"
    console.print(f"[info]Markdown Rendering:[/info] {md_state}")

    todos = cli.agent.todo_tool.get_todos()
    if todos:
        done = sum(1 for t in todos if t["status"] == "completed")
        console.print(f"[info]Task List:[/info] {done}/{len(todos)} completed (use /todos for details)")

    if cli._acp_manager is not None:
        statuses = cli._acp_manager.get_status()
        if statuses:
            running = sum(
                1 for s in statuses
                if s.state not in ("configured", "stopped", "failed")
            )
            inbox_n = cli._acp_manager.inbox.pending_count
            interact_n = cli._acp_manager.interactions.pending_count
            console.print(
                f"[info]ACP servers:[/info] {running}/{len(statuses)} running"
            )
            if inbox_n:
                console.print(f"[info]ACP inbox:[/info] {inbox_n} queued")
            if interact_n:
                console.print(
                    f"[warning]ACP interactions:[/warning] {interact_n} pending"
                )
    console.print()
