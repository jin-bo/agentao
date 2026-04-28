"""Input prompt, status bar, and main slash-command dispatch loop.

Split out from ``app.py`` to keep the CLI class slim. All functions
take the ``AgentaoCLI`` instance as their first argument.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import readchar
from prompt_toolkit.formatted_text import ANSI
from rich.markdown import Markdown

from ._globals import console

if TYPE_CHECKING:
    from .app import AgentaoCLI


# ── Input / Status bar ──────────────────────────────────────────────


def get_user_input(cli: "AgentaoCLI") -> str:
    import threading
    from prompt_toolkit.application.current import get_app_or_none
    from ..permissions import PermissionMode

    if cli._plan_session.is_active:
        prompt = ANSI("\n\033[1;35m[plan]\033[0m \033[1;36m❯\033[0m ")
    elif cli.current_mode == PermissionMode.READ_ONLY:
        prompt = ANSI("\n\033[1;31m[read-only]\033[0m \033[1;36m❯\033[0m ")
    else:
        prompt = ANSI("\n\033[1;36m❯\033[0m ")

    stop = threading.Event()
    app_ref: list = []

    def _pre_run() -> None:
        _app = get_app_or_none()
        if _app:
            app_ref.append(_app)

    def _ticker() -> None:
        while not stop.wait(1.0):
            if app_ref:
                app_ref[0].invalidate()

    ticker = threading.Thread(target=_ticker, daemon=True)
    ticker.start()
    try:
        return cli._prompt_session.prompt(prompt, pre_run=_pre_run)
    finally:
        stop.set()


def get_status_toolbar(cli: "AgentaoCLI") -> ANSI:
    from ..permissions import PermissionMode

    RST = "\x1b[0m"
    DIM = "\x1b[2m"

    try:
        model = cli.agent.get_current_model().split("/")[-1]
    except Exception:
        model = "—"

    if cli._plan_session.is_active:
        mode_col, mode_text = "\x1b[95m", "plan"
    elif cli.current_mode == PermissionMode.READ_ONLY:
        mode_col, mode_text = "\x1b[91m", "read-only"
    elif cli.current_mode == PermissionMode.FULL_ACCESS:
        mode_col, mode_text = "\x1b[92m", "full-access"
    else:
        mode_col, mode_text = "\x1b[96m", "workspace-write"

    pct = cli._cached_ctx_pct
    if pct >= 80:
        ctx_col = "\x1b[91m"
    elif pct >= 50:
        ctx_col = "\x1b[93m"
    else:
        ctx_col = "\x1b[37m"

    try:
        if cli.agent.bg_store is None:
            tasks = []
        else:
            tasks = cli.agent.bg_store.list()
        if tasks:
            import time as _time
            tokens = []
            for t in tasks:
                name = t.get("agent_name", "agent").replace("_", "-")
                st = t.get("status", "running")
                if st == "pending":
                    tokens.append(f"\x1b[2m⏳ {name} queued\x1b[0m")
                elif st == "running":
                    started = t.get("started_at")
                    elapsed = int(_time.time() - started) if started else 0
                    tokens.append(f"\x1b[93m⚙ {name} {elapsed}s\x1b[0m")
                elif st == "completed":
                    tokens.append(f"\x1b[32m✓ {name}\x1b[0m")
                elif st == "cancelled":
                    tokens.append(f"\x1b[2m⊘ {name}\x1b[0m")
                else:
                    tokens.append(f"\x1b[91m✗ {name}\x1b[0m")
            agents_part = f"  {DIM}│{RST}  " + f"  {DIM}·{RST}  ".join(tokens)
        else:
            agents_part = ""
    except Exception:
        agents_part = ""

    acp_part = ""
    try:
        if cli._acp_manager is not None:
            n_interact = cli._acp_manager.interactions.pending_count
            if n_interact:
                acp_part = (
                    f"  {DIM}│{RST}  "
                    f"\x1b[93m⏳ {n_interact} ACP interaction(s)\x1b[0m"
                )
    except Exception:
        pass

    rule = "\x1b[34m" + "─" * 300 + RST
    sep = f"  {DIM}│{RST}  "
    cwd = Path.cwd().name or str(Path.cwd())
    status = (
        f" {DIM}{model}{RST}"
        f"{sep}{mode_col}{mode_text}{RST}"
        f"{sep}{ctx_col}ctx {pct:.0f}%{RST}"
        f"{sep}{DIM}{cwd}{RST}"
        f"{agents_part}"
        f"{acp_part}"
    )
    return ANSI(f"{rule}\n{status}")


# ── Main loop ───────────────────────────────────────────────────────


def run_loop(cli: "AgentaoCLI") -> None:
    """Main input loop — slash-command dispatch + agent turn handling."""
    from .commands import (
        handle_todos_command, handle_plan_command, handle_provider_command,
        handle_model_command, handle_temperature_command, handle_context_command,
        handle_mcp_command, handle_permission_command, handle_sessions_command,
        handle_tools_command, handle_sandbox_command,
    )
    from .replay_commands import handle_replay_command
    from .commands_ext import (
        handle_crystallize_command, show_memories, handle_agent_command,
        _show_agents_dashboard, handle_acp_command,
    )
    from .subcommands import _handle_plugins_interactive

    cli.on_session_start()
    while True:
        try:
            cli._flush_acp_inbox()
            user_input = cli._get_user_input()

            if not user_input.strip():
                continue

            input_text = user_input.strip()

            if input_text.startswith('/'):
                parts = input_text[1:].split(maxsplit=1)
                command = parts[0].lower()
                args = parts[1] if len(parts) > 1 else ""

                if command in ["exit", "quit"]:
                    cli._save_session_on_exit()
                    console.print("\n[success]Goodbye![/success]\n")
                    break

                elif command == "help":
                    cli.print_help()
                    continue

                elif command == "clear":
                    cli.on_session_end()
                    cli.current_session_id = None
                    if cli._plan_session.is_active:
                        cli._plan_controller.exit_plan_mode()
                    cli.agent.clear_history()
                    cli.agent.memory_manager.clear()
                    cli.agent.memory_manager.clear_all_session_summaries()
                    cli.last_response = None
                    cli._cached_ctx_pct = 0.0
                    from ..permissions import PermissionMode
                    cli._apply_mode(PermissionMode.WORKSPACE_WRITE)
                    cli.on_session_start()
                    console.print("\n[success]Session and all memories cleared.[/success]")
                    console.print("[info]Permission mode reset to workspace-write.[/info]\n")
                    continue

                elif command == "new":
                    cli.on_session_end()
                    cli.current_session_id = None
                    if cli._plan_session.is_active:
                        cli._plan_controller.exit_plan_mode()
                    cli.agent.clear_history()
                    cli.last_response = None
                    cli._cached_ctx_pct = 0.0
                    from ..permissions import PermissionMode
                    cli._apply_mode(PermissionMode.WORKSPACE_WRITE)
                    cli.on_session_start()
                    console.print("\n[success]New session started. Long-term memories preserved.[/success]")
                    console.print("[info]Permission mode reset to workspace-write.[/info]\n")
                    continue

                elif command == "status":
                    cli.show_status()
                    continue

                elif command == "skills":
                    if not args:
                        cli.list_skills()
                    else:
                        sub_parts = args.split(maxsplit=1)
                        sub_cmd = sub_parts[0]
                        sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""
                        if sub_cmd == "activate":
                            if not sub_arg:
                                console.print("[warning]Usage: /skills activate <skill_name>[/warning]")
                            else:
                                result = cli.agent.skill_manager.activate_skill(
                                    sub_arg, "Manually activated via /skills activate"
                                )
                                if result.startswith("Error"):
                                    console.print(f"\n[warning]{result}[/warning]\n")
                                else:
                                    console.print(f"\n[success]Skill '{sub_arg}' activated.[/success]\n")
                        elif sub_cmd == "deactivate":
                            if not sub_arg:
                                console.print("[warning]Usage: /skills deactivate <skill_name>[/warning]")
                            elif sub_arg not in cli.agent.skill_manager.available_skills:
                                available = ", ".join(sorted(cli.agent.skill_manager.list_available_skills()))
                                console.print(f"[warning]Unknown skill '{sub_arg}'. Available: {available}[/warning]")
                            else:
                                deactivated = cli.agent.skill_manager.deactivate_skill(sub_arg)
                                if deactivated:
                                    console.print(f"\n[success]Skill '{sub_arg}' deactivated.[/success]\n")
                                else:
                                    console.print(f"\n[info]Skill '{sub_arg}' is not currently active.[/info]\n")
                        elif sub_cmd == "disable":
                            if not sub_arg:
                                console.print("[warning]Usage: /skills disable <skill_name>[/warning]")
                            else:
                                result = cli.agent.skill_manager.disable_skill(sub_arg)
                                console.print(f"\n{result}\n")
                        elif sub_cmd == "enable":
                            if not sub_arg:
                                console.print("[warning]Usage: /skills enable <skill_name>[/warning]")
                            else:
                                result = cli.agent.skill_manager.enable_skill(sub_arg)
                                console.print(f"\n{result}\n")
                        elif sub_cmd == "reload":
                            cli.agent.skill_manager.reload_skills()
                            count = len(cli.agent.skill_manager.list_available_skills())
                            console.print(f"\n[success]Skills reloaded. {count} available.[/success]\n")
                        else:
                            console.print(f"[warning]Unknown subcommand '{sub_cmd}'. Use: activate, deactivate, disable, enable, reload[/warning]")
                    continue

                elif command == "crystallize":
                    handle_crystallize_command(cli, args)
                    continue

                elif command == "memory":
                    if args:
                        subcommand_parts = args.split(maxsplit=1)
                        subcommand = subcommand_parts[0]
                        subcommand_arg = subcommand_parts[1] if len(subcommand_parts) > 1 else ""
                        show_memories(cli, subcommand, subcommand_arg)
                    else:
                        show_memories(cli)
                    continue

                elif command == "model":
                    handle_model_command(cli, args)
                    continue

                elif command == "provider":
                    handle_provider_command(cli, args)
                    continue

                elif command == "context":
                    handle_context_command(cli, args)
                    continue

                elif command == "mcp":
                    handle_mcp_command(cli, args)
                    continue

                elif command in ("plugins", "plugin"):
                    _handle_plugins_interactive()
                    continue

                elif command == "acp":
                    handle_acp_command(cli, args)
                    continue

                elif command == "agent":
                    handle_agent_command(cli, args)
                    continue

                elif command == "agents":
                    _show_agents_dashboard(cli)
                    continue

                elif command == "mode":
                    from ..permissions import PermissionMode
                    _valid = {m.value: m for m in PermissionMode if m != PermissionMode.PLAN}
                    if args == "":
                        console.print(f"\n[info]Permission mode:[/info] {cli.current_mode.value}\n")
                    elif args in _valid:
                        if cli._plan_session.is_active:
                            console.print("\n[warning]Cannot change permission mode while in plan mode.[/warning]")
                            console.print("[dim]Exit plan mode first with /plan implement or /plan clear.[/dim]\n")
                        else:
                            cli._apply_mode(_valid[args])
                            _descriptions = {
                                "read-only":       "write & shell tools are blocked",
                                "workspace-write": "file writes & safe shell allowed, web asks",
                                "full-access":     "all tools allowed without prompting",
                            }
                            console.print(f"\n[green]✓ Permission mode: {args}[/green]  [dim]({_descriptions.get(args, '')})[/dim]\n")
                    else:
                        console.print("\n[warning]Usage: /mode [read-only|workspace-write|full-access][/warning]\n")
                    continue

                elif command == "plan":
                    handle_plan_command(cli, args)
                    continue

                elif command == "copy":
                    _copy_last_response(cli)
                    continue

                elif command == "markdown":
                    cli.markdown_mode = not cli.markdown_mode
                    state = "ON" if cli.markdown_mode else "OFF"
                    console.print(f"\n[cyan]Markdown rendering: {state}[/cyan]\n")
                    continue

                elif command == "permission":
                    handle_permission_command(cli, args)
                    continue

                elif command == "sandbox":
                    handle_sandbox_command(cli, args)
                    continue

                elif command == "sessions":
                    handle_sessions_command(cli, args)
                    continue

                elif command == "temperature":
                    handle_temperature_command(cli, args)
                    continue

                elif command == "todos":
                    handle_todos_command(cli, args)
                    continue

                elif command == "tools":
                    handle_tools_command(cli, args)
                    continue

                elif command == "replay":
                    handle_replay_command(cli, args)
                    continue

                else:
                    console.print(f"\n[error]Unknown command: /{command}[/error]")
                    console.print("Type [cyan]/help[/cyan] for available commands.\n")
                    continue

            # Issue 12 Part A: explicit ACP server routing takes
            # priority over the normal agent path.
            if cli._try_acp_explicit_route(input_text):
                cli._flush_acp_inbox()
                continue

            # Process with agent
            console.rule("[bold green]Assistant[/bold green]", style="green")
            cli.current_status = console.status("[bold yellow]Thinking…", spinner="dots")
            cli.current_status.start()
            try:
                response = cli.agent.chat(user_input)
                cli.last_response = response
                try:
                    stats = cli.agent.context_manager.get_usage_stats(cli.agent.messages)
                    cli._cached_ctx_pct = stats.get("usage_percent", 0.0)
                except Exception:
                    pass
            except Exception:
                cli._streaming_started = False
                raise
            finally:
                if cli.current_status:
                    cli.current_status.stop()
                cli.current_status = None

            if cli._streaming_started:
                import sys
                sys.stdout.write("\n")
                sys.stdout.flush()
                cli._streaming_started = False
            else:
                console.print()
                if cli.markdown_mode:
                    console.print(Markdown(response))
                else:
                    console.print(response)

            cli._flush_acp_inbox()

            # Plan mode post-response handling
            if cli._plan_session.is_active:
                from ..plan.session import PlanPhase as _PlanPhase
                if cli._plan_session.phase != _PlanPhase.APPROVAL_PENDING:
                    if cli._plan_session.draft_id is None or (
                        response and cli._plan_session.draft != response.strip()
                    ):
                        auto_saved = cli._plan_controller.auto_save_response(response)
                        if auto_saved:
                            console.print(
                                f"[dim]Plan auto-saved → {cli._plan_session.current_plan_path}[/dim]"
                            )

            if cli._plan_session.consume_approval_request():
                _handle_plan_approval(cli)

        except KeyboardInterrupt:
            if cli.current_status:
                cli.current_status.stop()
                cli.current_status = None
            console.print("\n\n[warning]Interrupted. Type '/exit' to quit.[/warning]")
            continue

        except Exception as e:
            import traceback
            error_msg = str(e)
            cause = e.__cause__
            if cause:
                error_msg += f"\n  Caused by: {type(cause).__name__}: {cause}"
            console.print(f"\n[error]Error: {error_msg}[/error]")
            console.print("[dim]See agentao.log for full traceback.[/dim]\n")
            cli.agent.llm.logger.error(f"Unhandled error in chat loop:\n{traceback.format_exc()}")
            continue


def _copy_last_response(cli: "AgentaoCLI") -> None:
    if cli.last_response is None:
        console.print("\n[warning]No response to copy yet.[/warning]\n")
        return
    try:
        subprocess.run(
            ["pbcopy"], input=cli.last_response.encode(), check=True
        )
        console.print("\n[cyan]Copied to clipboard.[/cyan]\n")
    except FileNotFoundError:
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=cli.last_response.encode(), check=True
            )
            console.print("\n[cyan]Copied to clipboard.[/cyan]\n")
        except (FileNotFoundError, subprocess.CalledProcessError):
            try:
                subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=cli.last_response.encode(), check=True
                )
                console.print("\n[cyan]Copied to clipboard.[/cyan]\n")
            except (FileNotFoundError, subprocess.CalledProcessError):
                console.print("\n[error]No clipboard utility found (pbcopy/xclip/xsel).[/error]\n")
    except subprocess.CalledProcessError as e:
        console.print(f"\n[error]Copy failed: {e}[/error]\n")


def _handle_plan_approval(cli: "AgentaoCLI") -> None:
    _plan_draft = cli._plan_controller.show_draft()
    if _plan_draft:
        console.print(f"\n[dim]{cli._plan_session.current_plan_path}[/dim]\n")
        console.print(Markdown(_plan_draft) if cli.markdown_mode else _plan_draft)
        console.print()
    console.print("[bold magenta]Execute this plan?[/bold magenta] [dim][y/N][/dim] ", end="")
    try:
        _key = readchar.readkey()
        console.print()
        if _key in ("y", "Y"):
            restored, restore_allow_all = cli._plan_controller.exit_plan_mode()
            cli.allow_all_tools = restore_allow_all
            from ..permissions import PermissionMode as _PM
            if cli.current_mode == _PM.READ_ONLY:
                cli.current_mode = _PM.WORKSPACE_WRITE
                cli.permission_engine.set_mode(_PM.WORKSPACE_WRITE)
                cli.readonly_mode = False
                cli._apply_readonly_mode()
            console.rule("[bold green]Assistant[/bold green]", style="green")
            cli.current_status = console.status("[bold yellow]Thinking...", spinner="dots")
            cli.current_status.start()
            try:
                _exec_response = cli.agent.chat("Now implement the plan. Follow the steps you outlined above.")
                cli.last_response = _exec_response
            finally:
                if cli.current_status:
                    cli.current_status.stop()
                cli.current_status = None
            console.print()
            if cli.markdown_mode:
                console.print(Markdown(_exec_response))
            else:
                console.print(_exec_response)
            _pf = cli._plan_session.current_plan_path
            if _pf.exists():
                cli._plan_controller._archive_plan()
                _pf.unlink()
                console.print(f"[dim]Plan executed and archived.[/dim]\n")
        else:
            cli._plan_controller.reject_approval()
            console.print("[dim]Plan not approved. Continue refining or /plan implement when ready.[/dim]\n")
    except (KeyboardInterrupt, EOFError):
        cli._plan_controller.reject_approval()
        console.print()
