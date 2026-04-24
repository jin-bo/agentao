"""`/acp` slash command — lifecycle + prompt routing for ACP sub-servers (Issue 06)."""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING

import readchar

from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def _ensure_acp_manager(cli: AgentaoCLI):
    """Lazy-initialize the ACP manager on first /acp usage.

    Returns the manager (may be ``None`` if no config found).
    """
    if cli._acp_manager is not None:
        return cli._acp_manager

    try:
        from ...acp_client import ACPManager
        cli._acp_manager = ACPManager.from_project()
    except Exception as exc:
        console.print(f"\n[error]Failed to load ACP config: {exc}[/error]\n")
        return None
    return cli._acp_manager


def handle_acp_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /acp command and subcommands."""
    args = args.strip()
    parts = args.split(None, 1) if args else []
    sub = parts[0] if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if not sub or sub == "list":
        _acp_list(cli)
        return

    if sub == "start":
        _acp_start(cli, rest)
        return

    if sub == "stop":
        _acp_stop(cli, rest)
        return

    if sub == "restart":
        _acp_restart(cli, rest)
        return

    if sub == "send":
        _acp_send(cli, rest)
        return

    if sub == "cancel":
        _acp_cancel(cli, rest)
        return

    if sub == "status":
        _acp_status(cli, rest)
        return

    if sub == "logs":
        _acp_logs(cli, rest)
        return


    console.print(f"\n[error]Unknown subcommand: {sub}[/error]")
    console.print(
        "[info]Available: list, start, stop, restart, send, cancel, "
        "status, logs[/info]\n"
    )


def _acp_list(cli: AgentaoCLI) -> None:
    """List all configured ACP servers with state."""
    mgr = _ensure_acp_manager(cli)
    if mgr is None:
        return

    statuses = mgr.get_status()
    if not statuses:
        console.print(
            "\n[warning]No ACP servers configured.[/warning]"
            "\n[info]Add servers to .agentao/acp.json[/info]\n"
        )
        return

    running = sum(1 for s in statuses if s.state not in ("configured", "stopped", "failed"))
    total = len(statuses)
    inbox_n = mgr.inbox.pending_count
    interactions_n = mgr.interactions.pending_count

    console.print(f"\n[info]ACP Servers ({running}/{total} running):[/info]")
    if inbox_n:
        console.print(f"[info]Inbox:[/info] {inbox_n} queued")
    if interactions_n:
        console.print(f"[warning]Pending interactions:[/warning] {interactions_n}")
    console.print()

    _STATE_COLORS = {
        "configured": "dim",
        "starting": "yellow",
        "initializing": "yellow",
        "ready": "green",
        "busy": "cyan",
        "waiting_for_user": "magenta",
        "stopping": "yellow",
        "stopped": "dim",
        "failed": "red",
    }

    for s in statuses:
        handle = mgr.get_handle(s.server)
        description = handle.config.description if handle is not None else ""
        # Prefer the manager-recorded last_error from the ServerStatus
        # snapshot — it includes prompt-level failures (REQUEST_TIMEOUT,
        # INTERACTION_REQUIRED, …) that the process handle's
        # ``info.last_error`` doesn't see. Fall back to the handle's
        # error only when the snapshot has none, so process-level
        # failures that pre-date any turn still surface in /acp list.
        last_error = s.last_error
        if last_error is None and handle is not None:
            last_error = handle.info.last_error
        interactions_pending = s.interaction_pending

        color = _STATE_COLORS.get(s.state, "dim")
        desc = f"  [dim]{description}[/dim]" if description else ""
        pid_str = f" pid={s.pid}" if s.pid else ""
        err = f"  [red]{last_error}[/red]" if last_error else ""
        interact_str = ""
        if interactions_pending:
            interact_str = f"  [magenta]⏳ {interactions_pending} interaction(s)[/magenta]"
        console.print(
            f"  [{color}]●[/{color}] [cyan]{s.server}[/cyan] "
            f"[{color}]{s.state}[/{color}]{pid_str}{desc}{interact_str}{err}"
        )
    console.print()


def _acp_start(cli: AgentaoCLI, name: str) -> None:
    if not name:
        console.print("\n[error]Usage: /acp start <name>[/error]\n")
        return
    mgr = _ensure_acp_manager(cli)
    if mgr is None:
        return
    try:
        mgr.start_server(name)
        console.print(f"\n[success]ACP server '{name}' started.[/success]\n")
    except KeyError:
        console.print(f"\n[error]Unknown ACP server: {name}[/error]\n")
    except RuntimeError as exc:
        console.print(f"\n[error]Failed to start '{name}': {exc}[/error]\n")


def _acp_stop(cli: AgentaoCLI, name: str) -> None:
    if not name:
        console.print("\n[error]Usage: /acp stop <name>[/error]\n")
        return
    mgr = _ensure_acp_manager(cli)
    if mgr is None:
        return
    try:
        mgr.stop_server(name)
        console.print(f"\n[success]ACP server '{name}' stopped.[/success]\n")
    except KeyError:
        console.print(f"\n[error]Unknown ACP server: {name}[/error]\n")


def _acp_restart(cli: AgentaoCLI, name: str) -> None:
    if not name:
        console.print("\n[error]Usage: /acp restart <name>[/error]\n")
        return
    mgr = _ensure_acp_manager(cli)
    if mgr is None:
        return
    try:
        mgr.restart_server(name)
        console.print(f"\n[success]ACP server '{name}' restarted.[/success]\n")
    except KeyError:
        console.print(f"\n[error]Unknown ACP server: {name}[/error]\n")


def _handle_inline_interaction(cli, mgr, server_name: str, interaction) -> None:
    """Display an interaction and prompt the user inline during an active send.

    Uses readchar for single-key permission input and console.input() for
    free-form text input.  Runs on the main thread.
    """
    from ...acp_client.interaction import InteractionKind

    if interaction.kind == InteractionKind.PERMISSION:
        console.print(
            f"\n[bold yellow]Permission request from '{server_name}':[/bold yellow]"
        )
        # Extract structured tool call info if available.
        tool_call = None
        if interaction.details:
            tool_call = interaction.details.get("toolCall")

        if isinstance(tool_call, dict):
            title = tool_call.get("title") or "unknown tool"
            kind = tool_call.get("kind", "")
            kind_str = f" [dim]({kind})[/dim]" if kind else ""
            console.print(f"  [cyan]{title}[/cyan]{kind_str}")
            raw_input = tool_call.get("rawInput")
            if isinstance(raw_input, dict) and raw_input:
                for k, v in list(raw_input.items())[:6]:
                    val = str(v)
                    if len(val) > 80:
                        val = val[:77] + "..."
                    console.print(f"    {k}: [dim]{val}[/dim]")
                if len(raw_input) > 6:
                    console.print(f"    [dim]... +{len(raw_input) - 6} more[/dim]")
            content = tool_call.get("content")
            if isinstance(content, list):
                for entry in content[:2]:
                    if isinstance(entry, dict):
                        c = entry.get("content")
                        if isinstance(c, dict) and c.get("text"):
                            console.print(f"    [dim]{c['text'][:100]}[/dim]")
        else:
            prompt_text = interaction.prompt[:120] if interaction.prompt else "(no description)"
            console.print(f"  {prompt_text}")
        console.print(
            "\n [green]1[/green]. Approve once  "
            "[green]2[/green]. Approve all  "
            "[red]3[/red]. Reject once  "
            "[red]4[/red]. Reject all"
        )
        console.print(
            " [dim]Press 1-4 · Esc to reject[/dim]",
            end=" ",
        )
        while True:
            key = readchar.readkey()
            if key == "1":
                console.print("\n[green]Approved (once)[/green]")
                mgr.approve_interaction(server_name, interaction.request_id)
                return
            elif key == "2":
                console.print("\n[green]Approved (all future calls)[/green]")
                mgr.approve_interaction(
                    server_name, interaction.request_id, always=True
                )
                return
            elif key == "3":
                console.print("\n[red]Rejected (once)[/red]")
                mgr.reject_interaction(server_name, interaction.request_id)
                return
            elif key == "4":
                console.print("\n[red]Rejected (all future calls)[/red]")
                mgr.reject_interaction(
                    server_name, interaction.request_id, always=True
                )
                return
            elif key in (readchar.key.ESC, readchar.key.CTRL_C):
                console.print("\n[red]Rejected (cancelled)[/red]")
                mgr.reject_interaction(server_name, interaction.request_id)
                return

    elif interaction.kind == InteractionKind.INPUT:
        prompt_text = interaction.prompt if interaction.prompt else "(input requested)"
        console.print(
            f"\n[bold magenta]Input request from '{server_name}':[/bold magenta]"
        )
        console.print(f"  {prompt_text}")
        try:
            from prompt_toolkit import PromptSession as _PS
            from prompt_toolkit.formatted_text import ANSI as _ANSI
            _session = _PS()
            reply = _session.prompt(
                _ANSI("\033[1;35m> \033[0m")
            ).strip()
        except (EOFError, KeyboardInterrupt):
            reply = ""
        if reply:
            mgr.reply_interaction(server_name, interaction.request_id, reply)
        else:
            mgr.reject_interaction(server_name, interaction.request_id)
            console.print("[dim]Empty reply — cancelled.[/dim]")


def _acp_send(cli: AgentaoCLI, rest: str) -> None:
    """Slash entry point for ``/acp send <name> <message>``."""
    parts = rest.split(None, 1) if rest else []
    if len(parts) < 2:
        console.print("\n[error]Usage: /acp send <name> <message>[/error]\n")
        return
    name, message = parts[0], parts[1]
    run_acp_prompt_inline(cli, name, message)


def run_acp_prompt_inline(cli: AgentaoCLI, name: str, message: str) -> None:
    """Send a prompt to an ACP server with inline interaction handling.

    Shared runner used by both ``/acp send`` and the explicit-routing
    fast path (Issue 12, Part A) that triggers on ``@server-name``-style
    user input.

    Uses a non-blocking send so that permission/input requests from the
    server are displayed and resolved immediately, rather than deadlocking.
    """
    if not name or not message or not message.strip():
        console.print(
            "\n[error]ACP routing: missing server or empty task.[/error]\n"
        )
        return
    mgr = _ensure_acp_manager(cli)
    if mgr is None:
        return

    # Handle any pending interactions before sending a new prompt.
    pending = mgr.interactions.list_pending(server=name)
    for interaction in pending:
        _handle_inline_interaction(cli, mgr, name, interaction)

    # Track interactions so we only react to NEW ones during this send.
    seen_ids = {
        p.request_id for p in mgr.interactions.list_pending(server=name)
    }

    try:
        client, rid, slot = mgr.send_prompt_nonblocking(name, message)
    except KeyError:
        console.print(f"\n[error]Unknown ACP server: {name}[/error]\n")
        return
    except Exception as exc:
        # "active turn" error — cancel stale turn and retry once.
        if "already" in str(exc).lower() and "active" in str(exc).lower():
            console.print(f"[dim]Cancelling stale turn on '{name}'...[/dim]")
            mgr.cancel_turn(name)
            _time.sleep(0.5)
            try:
                client, rid, slot = mgr.send_prompt_nonblocking(name, message)
            except Exception as exc2:
                console.print(f"\n[error]Send failed after cancel: {exc2}[/error]\n")
                return
        else:
            console.print(f"\n[error]Send failed: {exc}[/error]\n")
            return

    timeout = client._handle.config.request_timeout_ms / 1000.0
    deadline = _time.time() + timeout
    spinner = console.status(
        f"[bold cyan]Sending to {name}...[/bold cyan]", spinner="dots"
    )
    spinner.start()

    def _drain_inbox() -> None:
        """Drain and display inbox messages that arrived during the turn."""
        msgs = mgr.flush_inbox()
        if msgs:
            spinner.stop()
            from ...acp_client.render import flush_to_console
            flush_to_console(msgs, console, markdown_mode=cli.markdown_mode)
            if not slot.event.is_set():
                spinner.start()

    try:
        while True:
            # Check if prompt completed.
            if slot.event.wait(timeout=0.3):
                spinner.stop()
                # Drain any remaining inbox messages.
                remaining = mgr.flush_inbox()
                if remaining:
                    from ...acp_client.render import flush_to_console
                    flush_to_console(remaining, console)

                try:
                    result = mgr.finish_prompt_nonblocking(name, client, rid, slot)
                except Exception as exc:
                    console.print(f"\n[error]Prompt failed: {exc}[/error]\n")
                    return
                stop_reason = (
                    result.get("stopReason", "unknown")
                    if isinstance(result, dict)
                    else "ok"
                )
                console.print(
                    f"\n[dim]{name}: turn finished "
                    f"({stop_reason})[/dim]\n"
                )
                return

            # Check timeout.
            if _time.time() >= deadline:
                spinner.stop()
                mgr.cancel_prompt_nonblocking(name, client, rid)
                console.print(
                    f"\n[error]Timeout waiting for response "
                    f"from '{name}'[/error]\n"
                )
                return

            # Drain inbox messages (tool calls, thoughts, text chunks).
            _drain_inbox()

            # Check for new interactions from this server.
            new_pending = [
                p
                for p in mgr.interactions.list_pending(server=name)
                if p.request_id not in seen_ids
            ]
            if not new_pending:
                continue

            # New interaction(s) — pause spinner and handle them.
            spinner.stop()

            for interaction in new_pending:
                seen_ids.add(interaction.request_id)
                _handle_inline_interaction(cli, mgr, name, interaction)

            # Reset deadline — user interaction time shouldn't count.
            deadline = _time.time() + timeout

            # Resume spinner if prompt hasn't completed yet.
            if not slot.event.is_set():
                spinner = console.status(
                    f"[bold cyan]Waiting for {name}...[/bold cyan]",
                    spinner="dots",
                )
                spinner.start()

    except KeyboardInterrupt:
        spinner.stop()
        mgr.cancel_prompt_nonblocking(name, client, rid)
        console.print(f"\n[warning]Cancelled prompt to '{name}'.[/warning]\n")
    except Exception as exc:
        try:
            spinner.stop()
        except Exception:
            pass
        # Make sure the per-server lock + turn slot are released even on
        # unexpected exceptions; cancel_prompt_nonblocking is idempotent.
        try:
            mgr.cancel_prompt_nonblocking(name, client, rid)
        except Exception:
            pass
        console.print(f"\n[error]Send failed: {exc}[/error]\n")


def _acp_cancel(cli: AgentaoCLI, name: str) -> None:
    if not name:
        console.print("\n[error]Usage: /acp cancel <name>[/error]\n")
        return
    mgr = _ensure_acp_manager(cli)
    if mgr is None:
        return
    try:
        mgr.cancel_turn(name)
        console.print(f"\n[success]Cancel sent to '{name}'.[/success]\n")
    except Exception as exc:
        console.print(f"\n[error]Cancel failed: {exc}[/error]\n")


def _acp_status(cli: AgentaoCLI, name: str) -> None:
    mgr = _ensure_acp_manager(cli)
    if mgr is None:
        return

    if not name:
        # Show overview
        _acp_list(cli)
        return

    handle = mgr.get_handle(name)
    if handle is None:
        console.print(f"\n[error]Unknown ACP server: {name}[/error]\n")
        return

    info = handle.info
    console.print(f"\n[info]ACP Server: {name}[/info]")
    console.print(f"  State:        {info.state.value}")
    console.print(f"  PID:          {info.pid or '—'}")
    console.print(f"  Description:  {handle.config.description or '—'}")
    if info.last_error:
        console.print(f"  [red]Last error:  {info.last_error}[/red]")
    if info.last_activity:
        elapsed = _time.time() - info.last_activity
        console.print(f"  Last activity: {elapsed:.0f}s ago")

    client = mgr.get_client(name)
    if client is not None:
        ci = client.connection_info
        console.print(f"  Session ID:   {ci.session_id or '—'}")
        console.print(f"  Protocol:     v{ci.protocol_version or '?'}")
        console.print(f"  Busy:         {client.is_busy}")

    # Pending interactions for this server
    pending = mgr.interactions.list_pending(server=name)
    if pending:
        console.print(f"\n  [warning]Pending interactions ({len(pending)}):[/warning]")
        for p in pending:
            console.print(
                f"    [{p.request_id}] {p.kind.value}: {p.prompt[:60]}"
            )

    # Recent stderr
    stderr_lines = handle.get_stderr_tail(5)
    if stderr_lines:
        console.print(f"\n  [dim]Recent stderr ({len(stderr_lines)} of last 5):[/dim]")
        for line in stderr_lines:
            console.print(f"    [dim]{line}[/dim]")
    console.print()


def _acp_logs(cli: AgentaoCLI, rest: str) -> None:
    """Show stderr logs for a server."""
    parts = rest.split() if rest else []
    if not parts:
        console.print("\n[error]Usage: /acp logs <name> [lines][/error]\n")
        return
    name = parts[0]
    n = 50
    if len(parts) > 1:
        try:
            n = int(parts[1])
        except ValueError:
            console.print(f"\n[error]Invalid line count: {parts[1]}[/error]\n")
            return

    mgr = _ensure_acp_manager(cli)
    if mgr is None:
        return

    try:
        lines = mgr.get_server_logs(name, n=n)
    except KeyError:
        console.print(f"\n[error]Unknown ACP server: {name}[/error]\n")
        return

    if not lines:
        console.print(f"\n[dim]No stderr output from '{name}'.[/dim]\n")
        return

    console.print(f"\n[info]Stderr log for '{name}' (last {len(lines)} lines):[/info]\n")
    for line in lines:
        console.print(f"  [dim]{line}[/dim]")
    console.print()
