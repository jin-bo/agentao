"""CLI display helpers: welcome banner, /help text, /skills, /status.

Split out from ``app.py`` to keep the class slim. All functions take
the ``AgentaoCLI`` instance as their first argument.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown

from ._globals import console
from .help_text import CLI_HELP_TEXT

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
    console.print(Markdown(CLI_HELP_TEXT))


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

    # Permission mode label is derived from the public
    # ``active_permissions()`` snapshot so the CLI reads the same
    # contract a host application would, instead of reaching into
    # private ``PermissionEngine`` state.
    snapshot = cli.agent.active_permissions()
    _mode_labels = {
        "read-only":       ("[red]read-only[/red]",       "write & shell tools are blocked"),
        "workspace-write": ("[green]workspace-write[/green]", "file writes & safe shell allowed, web asks"),
        "full-access":     ("[yellow]full-access[/yellow]",   "all tools allowed without prompting"),
        "plan":            ("[cyan]plan[/cyan]",              "research-only mode"),
    }
    _label, _desc = _mode_labels.get(snapshot.mode, (snapshot.mode, ""))
    console.print(f"[info]Permission Mode:[/info] {_label}  [dim]({_desc})[/dim]")
    if snapshot.loaded_sources:
        console.print(
            "[info]Loaded sources:[/info] "
            + ", ".join(f"[dim]{s}[/dim]" for s in snapshot.loaded_sources)
        )

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
