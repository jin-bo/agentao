"""`/agent` slash command — list, status, dashboard, cancel, bg-launch agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown
from rich.panel import Panel

from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def _show_agents_dashboard(cli: AgentaoCLI) -> None:
    """Render a live auto-refreshing table of all background agents."""
    import time as _time
    from rich.live import Live
    from rich.table import Table
    from rich import box as rich_box
    from rich.text import Text
    from ...agents.tools import list_bg_tasks

    def _fmt_status(t: dict) -> Text:
        status = t["status"]
        if status == "pending":
            return Text("◌  queued", style="dim")
        if status == "running":
            started = t.get("started_at")
            elapsed = _time.time() - started if started else 0
            return Text(f"○  {elapsed:.0f}s", style="yellow")
        if status == "completed":
            ms = t.get("duration_ms", 0)
            turns = t.get("turns", 0)
            calls = t.get("tool_calls", 0)
            tok = t.get("tokens", 0)
            tok_s = f"~{tok // 1000}k" if tok >= 1000 else str(tok)
            dur_s = f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"
            return Text(f"✓  {turns}t {calls}c {tok_s}  {dur_s}", style="green")
        if status == "cancelled":
            return Text("⊘  cancelled", style="dim")
        return Text("✗  failed", style="red")

    def _make_panel() -> Panel:
        tasks = list_bg_tasks()

        n_run    = sum(1 for t in tasks if t["status"] == "running")
        n_ok     = sum(1 for t in tasks if t["status"] == "completed")
        n_err    = sum(1 for t in tasks if t["status"] == "failed")
        n_cancel = sum(1 for t in tasks if t["status"] == "cancelled")

        tbl = Table(box=rich_box.SIMPLE, show_header=True, pad_edge=False,
                    header_style="bold dim")
        tbl.add_column("ID",     style="cyan",   width=9)
        tbl.add_column("Agent",  style="bold",   min_width=22, no_wrap=True)
        tbl.add_column("Status", min_width=22)
        tbl.add_column("Task",   style="dim",    ratio=1)

        for t in sorted(tasks, key=lambda x: x.get("created_at", 0), reverse=True):
            status_cell = _fmt_status(t)
            err_hint = ""
            if t["status"] == "failed" and t.get("error"):
                err_hint = f"  [dim red]{str(t['error'])[:60]}[/dim red]"
            task_cell = (t.get("task", "")[:55] or "") + err_hint
            tbl.add_row(t["id"], t["agent_name"], status_cell, task_cell)

        summary = (
            f"[yellow]○ {n_run} running[/yellow]  "
            f"[green]✓ {n_ok} completed[/green]  "
            f"{'[red]' if n_err else '[dim]'}✗ {n_err} failed{'[/red]' if n_err else '[/dim]'}  "
            f"[dim]⊘ {n_cancel} cancelled[/dim]"
        )
        footer = "[dim]Press Ctrl+C to exit[/dim]" if n_run else ""
        title = f"Background Agents  ·  {summary}"
        return Panel(tbl, title=title, subtitle=footer, border_style="cyan")

    tasks = list_bg_tasks()
    if not tasks:
        console.print("\n[dim]No background agents in this session.[/dim]\n")
        return

    active_statuses = {"pending", "running"}
    has_active = any(t["status"] in active_statuses for t in tasks)
    if not has_active:
        console.print()
        console.print(_make_panel())
        console.print()
        return

    try:
        with Live(_make_panel(), console=console, refresh_per_second=2,
                  vertical_overflow="visible") as live:
            while True:
                _time.sleep(0.5)
                live.update(_make_panel())
                if not any(t["status"] in active_statuses for t in list_bg_tasks()):
                    _time.sleep(0.3)
                    live.update(_make_panel())
                    break
    except KeyboardInterrupt:
        pass
    console.print()


def handle_agent_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /agent command."""
    from ...agents.tools import list_bg_tasks, get_bg_task
    import time as _time

    args = args.strip()
    parts = args.split(None, 1)
    sub = parts[0] if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if not sub or sub == "list":
        if not cli.agent.agent_manager:
            console.print("\n[warning]No agent manager available.[/warning]\n")
            return
        agents = cli.agent.agent_manager.list_agents()
        if not agents:
            console.print("\n[warning]No agents defined.[/warning]\n")
            return
        console.print(f"\n[info]Available Agents ({len(agents)}):[/info]\n")
        for name, desc in agents.items():
            console.print(f"  [cyan]{name}[/cyan]  [dim]{desc}[/dim]")
        console.print(
            "\n[dim]Usage: /agent <name> <task>  |  /agent bg <name> <task>"
            "  |  /agent dashboard[/dim]\n"
        )
        return

    if sub in ("dashboard", "dash"):
        _show_agents_dashboard(cli)
        return

    if sub == "status":
        agent_id = rest
        if not agent_id:
            tasks = list_bg_tasks()
            if not tasks:
                console.print("\n[dim]No background agents in this session.[/dim]\n")
                return
            console.print(f"\n[info]Background Agents ({len(tasks)}):[/info]\n")
            for t in tasks:
                status = t["status"]
                color = (
                    "dim" if status in ("pending", "cancelled")
                    else "yellow" if status == "running"
                    else "green" if status == "completed"
                    else "red"
                )
                started = t.get("started_at")
                finished = t.get("finished_at")
                if finished and started:
                    elapsed = f"{finished - started:.1f}s"
                elif started:
                    elapsed = f"{_time.time() - started:.0f}s"
                elif status == "cancelled" and finished:
                    elapsed = "cancelled before start"
                else:
                    elapsed = "queued"
                console.print(
                    f"  [{color}]{status:<10}[/{color}]  [cyan]{t['id']}[/cyan]"
                    f"  [bold]{t['agent_name']}[/bold]  ({elapsed})"
                    f"  [dim]{t['task'][:60]}[/dim]"
                )
            console.print()
        else:
            rec = get_bg_task(agent_id)
            if rec is None:
                console.print(f"\n[error]No background agent with ID: {agent_id}[/error]\n")
                return
            status = rec["status"]
            color = (
                "dim" if status in ("pending", "cancelled")
                else "yellow" if status == "running"
                else "green" if status == "completed"
                else "red"
            )
            console.print(f"\n[info]Agent:[/info] [bold]{rec['agent_name']}[/bold]  ID: [cyan]{agent_id}[/cyan]")
            console.print(f"[info]Status:[/info] [{color}]{status}[/{color}]")
            console.print(f"[info]Task:[/info]   {rec['task']}")
            if rec.get("finished_at") and rec.get("started_at"):
                elapsed = rec["finished_at"] - rec["started_at"]
                console.print(f"[info]Time:[/info]   {elapsed:.1f}s")
            elif status == "cancelled" and rec.get("finished_at") and rec.get("started_at") is None:
                console.print("[info]Time:[/info]   cancelled before start")
            elif rec.get("started_at") is None:
                console.print("[info]Time:[/info]   not started yet")
            if status == "completed" and rec.get("result"):
                console.print("\n[info]Result:[/info]")
                console.print(Markdown(rec["result"]))
            elif status == "failed" and rec.get("error"):
                console.print(f"\n[error]Error:[/error] {rec['error']}")
            elif status == "cancelled":
                console.print("\n[dim]Agent was cancelled.[/dim]")
            console.print()
        return

    if sub == "cancel":
        agent_id = rest.strip()
        if not agent_id:
            console.print("\n[error]Usage: /agent cancel <agent-id>[/error]\n")
            return
        from ...agents.tools import _cancel_bg_task
        msg = _cancel_bg_task(agent_id)
        console.print(f"\n{msg}\n")
        return

    if sub == "delete":
        agent_id = rest.strip()
        if not agent_id:
            console.print("\n[error]Usage: /agent delete <agent-id>[/error]\n")
            return
        from ...agents.tools import _delete_bg_task
        msg = _delete_bg_task(agent_id)
        console.print(f"\n{msg}\n")
        return

    if sub == "bg":
        bg_parts = rest.split(None, 1)
        if len(bg_parts) < 2:
            console.print("\n[error]Usage: /agent bg <agent-name> <task>[/error]\n")
            return
        agent_name, task = bg_parts[0], bg_parts[1]
        tool_name = f"agent_{agent_name.replace('-', '_')}"
        try:
            tool = cli.agent.tools.get(tool_name)
        except KeyError:
            console.print(f"\n[error]Unknown agent: {agent_name}[/error]\n")
            return
        msg = tool.execute(task=task, run_in_background=True)
        console.print(f"\n[cyan]{msg}[/cyan]\n")
        return

    # /agent <name> <task>  (foreground)
    agent_name = sub
    if not rest:
        console.print(f"\n[error]Usage: /agent {agent_name} <task description>[/error]\n")
        return

    tool_name = f"agent_{agent_name.replace('-', '_')}"
    try:
        tool = cli.agent.tools.get(tool_name)
    except KeyError:
        console.print(f"\n[error]Unknown agent: {agent_name}[/error]")
        available = ", ".join(cli.agent.agent_manager.list_agents().keys()) if cli.agent.agent_manager else ""
        console.print(f"[info]Available: {available}[/info]\n")
        return

    cli.current_status = console.status(
        f"[bold cyan][{agent_name}] Thinking...[/bold cyan]", spinner="dots"
    )
    with cli.current_status:
        result = tool.execute(task=rest)

    console.print(Markdown(result))
