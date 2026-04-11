"""Transport protocol callback implementations for AgentaoCLI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import readchar

from ..transport import AgentEvent, EventType
from ._globals import console

if TYPE_CHECKING:
    from .app import AgentaoCLI


def emit_event(cli: AgentaoCLI, event: AgentEvent) -> None:
    """Dispatch a runtime event to the appropriate handler."""
    try:
        t = event.type
        if t == EventType.TURN_START:
            if cli.current_status:
                cli.current_status.update("[bold yellow]Thinking…[/bold yellow]")
        elif t == EventType.TOOL_CONFIRMATION:
            if cli.current_status:
                tool_name = event.data.get("tool", "")
                cli.current_status.update(
                    f"[yellow]Waiting for confirmation…  [dim]{tool_name}[/dim][/yellow]"
                )
        elif t == EventType.THINKING:
            on_llm_thinking(cli, event.data.get("text", ""))
        elif t == EventType.LLM_TEXT:
            on_llm_text(cli, event.data.get("chunk", ""))
        else:
            cli.display.on_event(event)
    except Exception:
        pass  # never let a UI error crash the runtime


def confirm_tool_execution(cli: AgentaoCLI, tool_name: str, tool_description: str, tool_args: dict) -> bool:
    """Prompt user to confirm tool execution with menu options."""
    from ..permissions import PermissionMode

    if (cli.current_mode == PermissionMode.FULL_ACCESS or cli.allow_all_tools) and not cli._plan_session.is_active:
        return True

    if cli.current_status:
        cli.current_status.stop()

    try:
        console.print(f"\n[yellow]⚠️  Tool Confirmation Required[/yellow]")
        console.print(f"[info]Tool:[/info] [cyan]{tool_name}[/cyan]")
        console.print(f"[info]Arguments:[/info]")

        for key, value in tool_args.items():
            console.print(f"  • {key}: {value}")

        console.print("\n[bold]Choose an option:[/bold]")
        console.print(" [green]1[/green]. Yes")
        console.print(" [green]2[/green]. Yes, allow all tools during this session")
        console.print(" [red]3[/red]. No")
        console.print("\n[dim]Press 1, 2, or 3 (single key, no Enter needed) · Esc to cancel[/dim]", end=" ")

        while True:
            try:
                key = readchar.readkey()

                if key == "1":
                    console.print("\n[green]✓ Executing tool[/green]")
                    return True
                elif key == "2":
                    from ..permissions import PermissionMode
                    cli.allow_all_tools = True
                    cli.current_mode = PermissionMode.FULL_ACCESS
                    cli.permission_engine.set_mode(PermissionMode.FULL_ACCESS)
                    cli.readonly_mode = False
                    cli._apply_readonly_mode()
                    console.print("\n[green]✓ Executing tool (full-access mode enabled for this session)[/green]")
                    return True
                elif key == "3":
                    console.print("\n[red]✗ Cancelled[/red]")
                    return False
                elif key == readchar.key.ESC:
                    console.print("\n[red]✗ Cancelled[/red]")
                    return False
                elif key == readchar.key.CTRL_C:
                    console.print("\n[red]✗ Cancelled[/red]")
                    return False
                else:
                    continue

            except KeyboardInterrupt:
                console.print("\n[red]✗ Cancelled[/red]")
                return False
            except Exception as e:
                console.print(f"\n[red]✗ Cancelled (error: {e})[/red]")
                return False

    finally:
        if cli.current_status:
            cli.current_status.start()


def on_llm_thinking(cli: AgentaoCLI, reasoning: str) -> None:
    """Display LLM reasoning text produced before tool calls."""
    if not reasoning.strip():
        return

    if cli.current_status:
        cli.current_status.stop()

    console.rule("[dim]Thinking[/dim]", style="dim blue")
    for line in reasoning.strip().splitlines():
        console.print(f"  [dim italic]{line}[/dim italic]")
    console.print()

    if cli.current_status:
        cli.current_status.start()


def on_max_iterations(cli: AgentaoCLI, max_iterations: int, pending_tools: list) -> dict:
    """Called when tool call loop reaches max iterations."""
    if cli.current_status:
        cli.current_status.stop()
    try:
        console.print(f"\n[bold yellow]⚠️  已达到最大工具调用次数 ({max_iterations})[/bold yellow]")

        if pending_tools:
            console.print("[dim]待执行的工具调用：[/dim]")
            for tc in pending_tools:
                try:
                    args = json.loads(tc["args"]) if isinstance(tc["args"], str) else tc["args"]
                    args_str = ", ".join(f"{k}={repr(v)}" for k, v in list(args.items())[:3])
                except Exception:
                    args_str = str(tc["args"])[:80]
                console.print(f"  • [cyan]{tc['name']}[/cyan]({args_str})")
        else:
            console.print("[dim]无待执行的工具调用。[/dim]")

        console.print("\n[bold]选择操作：[/bold]")
        console.print(" [green]1[/green]. 继续（重置计数器，再执行 100 次）")
        console.print(" [red]2[/red]. 停止")
        console.print(" [yellow]3[/yellow]. 输入新的工作指令后继续")
        console.print("\n[dim]按 1、2 或 3（单键，无需回车）· Esc 停止[/dim]", end=" ")

        while True:
            try:
                key = readchar.readkey()
                if key == "1":
                    console.print("\n[green]✓ 继续执行[/green]")
                    return {"action": "continue"}
                elif key == "2" or key in (readchar.key.ESC, readchar.key.CTRL_C):
                    console.print("\n[red]✗ 停止[/red]")
                    return {"action": "stop"}
                elif key == "3":
                    console.print()
                    new_msg = console.input("[bold yellow]▶ 新指令：[/bold yellow]").strip()
                    if not new_msg:
                        new_msg = "继续"
                    return {"action": "new_instruction", "message": new_msg}
                else:
                    continue
            except KeyboardInterrupt:
                console.print("\n[red]✗ 停止[/red]")
                return {"action": "stop"}
    finally:
        if cli.current_status:
            cli.current_status.start()


def on_llm_text(cli: AgentaoCLI, chunk: str) -> None:
    """Handle LLM text chunk (streaming mode)."""
    if cli.markdown_mode:
        return  # batch-render at end; streaming raw markup is unreadable
    import sys
    if not cli._streaming_started:
        if cli.current_status:
            cli.current_status.stop()
            cli.current_status = None
        sys.stdout.write("\n")
        sys.stdout.flush()
        cli._streaming_started = True
    sys.stdout.write(chunk)
    sys.stdout.flush()


def ask_user(cli: AgentaoCLI, question: str) -> str:
    """Pause spinner, display question, read free-form user response."""
    if cli.current_status:
        cli.current_status.stop()
    try:
        console.print(f"\n[bold yellow]🤔 Agent Question[/bold yellow]")
        console.print(f"[yellow]{question}[/yellow]")
        response = console.input("[bold yellow]▶ [/bold yellow]").strip()
        return response if response else "(no response)"
    except (EOFError, KeyboardInterrupt):
        return "(user interrupted)"
    finally:
        if cli.current_status:
            cli.current_status.start()
