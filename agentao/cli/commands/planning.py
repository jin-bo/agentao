"""``/todos`` and ``/plan`` slash commands.

Both surfaces are workflow-shaped: the agent maintains task state and
plan-mode is a read-only-then-implement two-phase flow. They share no
state with the runtime config commands and stay together because users
think of them together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown

from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def handle_todos_command(cli: AgentaoCLI, args: str = "") -> None:
    """Display the current task list."""
    todos = cli.agent.todo_tool.get_todos()
    if not todos:
        console.print(
            "\n[info]No tasks.[/info] [dim]The LLM will create tasks automatically "
            "when handling complex multi-step requests.[/dim]\n"
        )
        return

    done = sum(1 for t in todos if t["status"] == "completed")
    console.print(f"\n[info]Task List ({done}/{len(todos)} completed):[/info]\n")
    _icons = {"pending": "○", "in_progress": "◉", "completed": "✓"}
    _colors = {"pending": "white", "in_progress": "yellow", "completed": "green"}
    for todo in todos:
        status = todo["status"]
        icon = _icons.get(status, "○")
        color = _colors.get(status, "white")
        console.print(f"  [{color}]{icon}[/{color}] {todo['content']} [dim]{status}[/dim]")
    console.print()


def handle_plan_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /plan command and subcommands — thin dispatch to PlanController."""
    _plan_file = cli._plan_session.current_plan_path
    args = args.strip()

    if args == "show":
        content = cli._plan_controller.show_draft()
        if content is None:
            console.print("\n[warning]No plan file found. The agent saves it automatically when in plan mode.[/warning]\n")
            return
        console.print(f"\n[dim]{_plan_file}[/dim]\n")
        console.print(Markdown(content) if cli.markdown_mode else content)
        console.print()
        return

    if args == "clear":
        if not _plan_file.exists() and not cli._plan_session.is_active:
            console.print("\n[info]No plan file to clear.[/info]\n")
            return
        was_active = cli._plan_session.is_active
        restored, restore_allow_all = cli._plan_controller.archive_and_clear()
        if was_active:
            cli.allow_all_tools = restore_allow_all
            console.print("\n[success]Plan archived and cleared. Plan mode OFF.[/success]\n")
        else:
            console.print("\n[success]Plan archived and cleared.[/success]\n")
        return

    if args == "implement":
        if not cli._plan_session.is_active:
            console.print("\n[info]Not in plan mode.[/info]\n")
            return
        restored, restore_allow_all = cli._plan_controller.exit_plan_mode()
        cli.allow_all_tools = restore_allow_all
        console.print(f"\n[success]Plan mode OFF. Permission mode: {restored.value}[/success]")
        if _plan_file.exists():
            content = _plan_file.read_text(encoding="utf-8")
            console.print(f"\n[dim]Current plan ({_plan_file}):[/dim]\n")
            console.print(Markdown(content) if cli.markdown_mode else content)
            console.print("\n[dim]Ask the agent to implement the plan above.[/dim]\n")
        else:
            console.print("\n[warning]No saved plan file. Describe the plan in your next message.[/warning]\n")
        return

    if args == "":
        if cli._plan_session.is_active:
            console.print("\n[bold magenta][plan mode is ON][/bold magenta]")
            content = cli._plan_controller.show_draft()
            if content:
                console.print(f"[dim]Saved plan: {_plan_file}[/dim]\n")
                console.print(Markdown(content) if cli.markdown_mode else content)
            else:
                console.print("[dim]No plan saved yet.[/dim]")
            console.print("\n[dim]/plan show · /plan implement · /plan clear[/dim]\n")
            return
        # Enter plan mode
        cli._plan_controller.enter(cli.current_mode, cli.allow_all_tools)
        cli.allow_all_tools = False
        cli.readonly_mode = False
        cli._apply_readonly_mode()
        console.print("\n[bold magenta]Plan mode ON[/bold magenta]  [dim](read-only; LLM will plan, not execute)[/dim]")
        console.print("[dim]Ask what to plan. When done: /plan implement · /plan clear[/dim]\n")
        return

    if args == "history":
        entries = cli._plan_controller.list_history()
        if not entries:
            console.print("\n[info]No plan history yet.[/info]\n")
            return
        console.print("\n[bold]Plan history[/bold] [dim](most recent first)[/dim]\n")
        for entry in entries:
            context_snippet = ""
            try:
                text = entry.read_text(encoding="utf-8")
                import re as _re
                m = _re.search(r"##\s+Context\s*\n(.*?)(?=\n##|\Z)", text, _re.DOTALL)
                if m:
                    snippet = " ".join(m.group(1).split())
                    if len(snippet) > 160:
                        snippet = snippet[:159] + "..."
                    context_snippet = f"  [dim]{snippet}[/dim]"
            except Exception:
                pass
            console.print(f"  [bold]{entry.stem}[/bold]")
            if context_snippet:
                console.print(context_snippet)
        console.print()
        return

    console.print(f"\n[error]Unknown: /plan {args}[/error]\n[info]Usage: /plan | /plan show | /plan implement | /plan clear | /plan history[/info]\n")
