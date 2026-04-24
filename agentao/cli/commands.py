"""Slash command handlers for AgentaoCLI (core set)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import readchar
from rich.markdown import Markdown

from ._globals import console

if TYPE_CHECKING:
    from .app import AgentaoCLI


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


def _list_providers_from_env() -> list:
    """Return sorted list of provider names that have API key, base URL, and model in environment."""
    providers = []
    for key, value in os.environ.items():
        if key.endswith("_API_KEY") and value:
            provider = key[: -len("_API_KEY")]
            if os.getenv(f"{provider}_BASE_URL") and os.getenv(f"{provider}_MODEL"):
                providers.append(provider)
    return sorted(providers)


def handle_provider_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /provider command."""
    args = args.strip().upper()

    if not args:
        current_model = cli.agent.get_current_model()
        console.print(f"\n[info]Current Provider:[/info] [cyan]{cli.current_provider}[/cyan]  "
                      f"[dim](model: {current_model})[/dim]\n")

        providers = _list_providers_from_env()
        if not providers:
            console.print("[warning]No providers found in .env (looking for XXXX_API_KEY entries)[/warning]\n")
            return

        console.print("[info]Available Providers:[/info]")
        for p in providers:
            marker = " [green]✓[/green]" if p == cli.current_provider else ""
            console.print(f"  • {p}{marker}")
        console.print("\n[info]Usage:[/info] /provider <NAME>  (e.g. /provider GEMINI)\n")

    else:
        api_key = os.getenv(f"{args}_API_KEY")
        if not api_key:
            console.print(f"\n[error]No API key found for provider '{args}' "
                           f"(expected env var: {args}_API_KEY)[/error]\n")
            return

        base_url = os.getenv(f"{args}_BASE_URL") or None
        if not base_url:
            console.print(f"\n[error]No base URL configured for provider '{args}' "
                           f"(expected env var: {args}_BASE_URL, "
                           f"e.g. {args}_BASE_URL=https://api.openai.com/v1)[/error]\n")
            return

        model = os.getenv(f"{args}_MODEL") or None
        if not model:
            console.print(f"\n[error]No model configured for provider '{args}' "
                           f"(expected env var: {args}_MODEL, e.g. {args}_MODEL=gpt-5.4)[/error]\n")
            return

        cli.agent.set_provider(api_key=api_key, base_url=base_url, model=model)
        cli.current_provider = args

        current_model = cli.agent.get_current_model()
        console.print(f"\n[success]Switched to provider: {args}[/success]")
        console.print(f"[info]Model:[/info] [cyan]{current_model}[/cyan]\n")


def handle_model_command(cli: AgentaoCLI, args: str) -> None:
    """Handle model command."""
    args = args.strip()

    if not args:
        current = cli.agent.get_current_model()
        console.print(f"\n[info]Current Model:[/info] [cyan]{current}[/cyan]\n")
        try:
            with console.status("[dim]Fetching available models…[/dim]"):
                available = cli.agent.list_available_models()
        except RuntimeError as e:
            console.print(f"[error]Failed to list models: {e}[/error]\n")
            return

        console.print("[info]Available Models:[/info]\n")

        claude_models = [m for m in available if m.startswith("claude-")]
        gpt_models = [m for m in available if m.startswith("gpt-")]
        other_models = [m for m in available if not m.startswith(("claude-", "gpt-"))]

        if claude_models:
            console.print("  [bold]Claude:[/bold]")
            for model in claude_models:
                marker = " [green]✓[/green]" if model == current else ""
                console.print(f"    • {model}{marker}")

        if gpt_models:
            console.print("\n  [bold]OpenAI GPT:[/bold]")
            for model in gpt_models:
                marker = " [green]✓[/green]" if model == current else ""
                console.print(f"    • {model}{marker}")

        if other_models:
            console.print("\n  [bold]Other:[/bold]")
            for model in other_models:
                marker = " [green]✓[/green]" if model == current else ""
                console.print(f"    • {model}{marker}")

        console.print("\n[info]Usage:[/info] /model <model_name>")
        console.print("Example: /model claude-sonnet-4-6\n")

    else:
        result = cli.agent.set_model(args)
        console.print(f"\n[success]{result}[/success]\n")


def handle_temperature_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /temperature command — show or set LLM temperature."""
    args = args.strip()
    if not args:
        console.print(f"\n[info]Temperature:[/info] [cyan]{cli.agent.llm.temperature}[/cyan]")
        console.print("[dim]Usage: /temperature <value>  (0.0 - 2.0)[/dim]\n")
        return
    try:
        value = float(args)
    except ValueError:
        console.print(f"\n[error]Invalid temperature value: {args}[/error]\n")
        return
    if not 0.0 <= value <= 2.0:
        console.print("\n[error]Temperature must be between 0.0 and 2.0[/error]\n")
        return
    old = cli.agent.llm.temperature
    cli.agent.llm.temperature = value
    console.print(f"\n[success]Temperature changed from {old} to {value}[/success]\n")


def handle_context_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /context command."""
    args = args.strip()
    cm = cli.agent.context_manager

    if not args:
        stats = cm.get_usage_stats(cli.agent.messages)
        console.print("\n[info]Context Window Status:[/info]")
        console.print(f"  Estimated tokens: [cyan]{stats['estimated_tokens']:,}[/cyan]")
        console.print(f"  Max tokens:       [cyan]{stats['max_tokens']:,}[/cyan]")

        pct = stats["usage_percent"]
        color = "green" if pct < 55 else "yellow" if pct < 65 else "red"
        console.print(f"  Usage:            [{color}]{pct:.1f}%[/{color}]")
        console.print(f"  Messages:         {stats['message_count']}")

        failures = stats.get("circuit_breaker_failures", 0)
        if failures > 0:
            fb_color = "yellow" if failures < cm.CIRCUIT_BREAKER_LIMIT else "red"
            console.print(
                f"  Compact failures: [{fb_color}]{failures}/{cm.CIRCUIT_BREAKER_LIMIT}[/{fb_color}]"
                + (" [dim](circuit open — auto-compact disabled)[/dim]"
                   if failures >= cm.CIRCUIT_BREAKER_LIMIT else "")
            )

        lc = stats.get("last_compact")
        if lc:
            pre = lc.get("pre_compact_tokens", 0)
            post = lc.get("post_compact_tokens", 0)
            summarized = lc.get("messages_summarized", 0)
            kept = lc.get("messages_kept", 0)
            ts = lc.get("timestamp", "")[:19]
            console.print(
                f"  Last compact:     {ts}  "
                f"[dim]{pre:,} → {post:,} tokens | "
                f"{summarized} summarized, {kept} kept[/dim]"
            )
            files = lc.get("recently_read_files", [])
            if files:
                console.print(f"  Re-injected files: [dim]{', '.join(files[:5])}[/dim]")
        console.print()

    elif args.startswith("limit "):
        limit_str = args[6:].strip()
        try:
            new_limit = int(limit_str)
            if new_limit < 1000:
                console.print("\n[error]Context limit must be at least 1,000 tokens[/error]\n")
                return
            cm.max_tokens = new_limit
            console.print(f"\n[success]Context limit set to {new_limit:,} tokens[/success]\n")
        except ValueError:
            console.print(f"\n[error]Invalid number: {limit_str}[/error]\n")
    else:
        console.print("\n[error]Usage: /context  OR  /context limit <n>[/error]\n")


def handle_mcp_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /mcp command for MCP server management."""
    from ..mcp.config import load_mcp_config, save_mcp_config, _load_json_file

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

        project_path = Path.cwd() / ".agentao" / "mcp.json"
        existing = _load_json_file(project_path)
        servers = existing.get("mcpServers", {})
        servers[name] = server_cfg
        saved_path = save_mcp_config(servers)

        console.print(f"\n[success]Added MCP server '{name}' to {saved_path}[/success]")
        console.print("[info]Restart agentao to connect to the new server.[/info]\n")

    elif sub == "remove":
        name = sub_args.strip()
        if not name:
            console.print("\n[error]Usage: /mcp remove <name>[/error]\n")
            return

        project_path = Path.cwd() / ".agentao" / "mcp.json"
        existing = _load_json_file(project_path)
        servers = existing.get("mcpServers", {})
        if name not in servers:
            console.print(f"\n[warning]Server '{name}' not found in config.[/warning]\n")
            return

        del servers[name]
        save_mcp_config(servers)
        console.print(f"\n[success]Removed MCP server '{name}'.[/success]")
        console.print("[info]Restart agentao to apply changes.[/info]\n")

    else:
        console.print(f"\n[error]Unknown subcommand: {sub}[/error]")
        console.print("[info]Available: /mcp list, /mcp add, /mcp remove[/info]\n")


def handle_permission_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /permission command — show active permission rules."""
    console.print(f"\n{cli.permission_engine.get_rules_display()}\n")


def handle_sandbox_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /sandbox command — macOS sandbox-exec control.

    Subcommands:
        /sandbox [status]             Show current sandbox state.
        /sandbox on                   Enable for this session.
        /sandbox off                  Disable for this session.
        /sandbox profile <name>       Switch profile (session only).
        /sandbox profiles             List available profile names.
    """
    policy = getattr(cli.agent, "sandbox_policy", None)
    if policy is None:
        console.print("\n[warning]Sandbox policy not initialized on this agent.[/warning]\n")
        return

    parts = args.strip().split(None, 1)
    sub = parts[0].lower() if parts else "status"
    sub_arg = parts[1].strip() if len(parts) > 1 else ""

    if sub in ("", "status"):
        supported = policy.platform_supported
        health = policy.health_error()
        if policy.enabled and health is None:
            state = "[green]enabled[/green]"
        elif policy.enabled and health is not None:
            state = f"[red]enabled but BROKEN[/red] [warning]({health})[/warning]"
        else:
            state = "[dim]disabled[/dim]"
        if not supported:
            state += "  [warning](platform not supported — macOS only)[/warning]"
        console.print(f"\n[info]Sandbox:[/info] {state}")
        console.print(f"  Default profile: [cyan]{policy.default_profile_name}[/cyan]")
        console.print(f"  Workspace root:  [dim]{policy.workspace_root}[/dim]")
        console.print(f"  Available:       {', '.join(policy.list_profiles()) or '(none)'}")
        rule_profile = policy.rule_profile_for("run_shell_command")
        if rule_profile is not None:
            console.print(
                f"  [dim]Note: a rule maps run_shell_command → "
                f"[cyan]{rule_profile}[/cyan] (overrides default_profile)[/dim]"
            )
        if policy.enabled and health is not None:
            console.print(
                f"\n[warning]Shell commands will FAIL while the sandbox is "
                f"broken (fail-closed). Fix the config or run /sandbox off.[/warning]"
            )
        console.print()
        return

    if sub == "on":
        if not policy.platform_supported:
            console.print("\n[warning]Sandbox is macOS-only. Cannot enable on this platform.[/warning]\n")
            return
        policy.set_enabled(True)
        health = policy.health_error()
        if health is not None:
            console.print(
                f"\n[warning]⚠ Sandbox marked enabled, but the config is broken: "
                f"{health}[/warning]"
            )
            console.print(
                f"[warning]Shell commands will FAIL (fail-closed) until this is "
                f"fixed. Use `/sandbox profile <name>` to pick a valid profile, "
                f"or `/sandbox off` to disable.[/warning]\n"
            )
            return
        console.print(
            f"\n[green]✓ Sandbox enabled[/green] "
            f"(profile: [cyan]{policy.default_profile_name}[/cyan], session only — "
            f"edit .agentao/sandbox.json to persist)\n"
        )
        return

    if sub == "off":
        policy.set_enabled(False)
        console.print("\n[cyan]Sandbox disabled for this session.[/cyan]\n")
        return

    if sub in ("profile", "profiles"):
        if sub == "profiles" or not sub_arg:
            console.print(f"\n[info]Available profiles:[/info] {', '.join(policy.list_profiles()) or '(none)'}\n")
            return
        # Preflight against sandbox-exec — not just file existence. A
        # malformed custom .sb would pass is_file() but every subsequent
        # run_shell_command would die with "Invalid sandbox profile", and
        # the user would have no idea the switch was bogus. Reject up
        # front, the same way /sandbox on and /sandbox status do.
        health = policy.profile_health_error(sub_arg)
        if health is not None:
            console.print(f"\n[warning]{health}[/warning]\n")
            return
        resolved = policy._locate_profile(sub_arg)  # type: ignore[attr-defined]
        policy.set_default_profile(sub_arg)
        console.print(f"\n[green]✓ Default profile → [cyan]{sub_arg}[/cyan][/green]  [dim]({resolved})[/dim]")
        # Warn if a per-tool rule shadows the default for shell commands —
        # otherwise the user thinks they switched profiles but resolve()
        # keeps returning the rule's profile.
        rule_profile = policy.rule_profile_for("run_shell_command")
        if rule_profile is not None and rule_profile != sub_arg:
            console.print(
                f"[warning]⚠ A rule in .agentao/sandbox.json maps "
                f"run_shell_command → '{rule_profile}' and takes precedence "
                f"over default_profile. Shell commands will keep using "
                f"'{rule_profile}'. Edit the rule to make this switch "
                f"effective.[/warning]"
            )
        console.print()
        return

    console.print(f"\n[error]Unknown subcommand: /sandbox {sub}[/error]")
    console.print("[info]Available: /sandbox status | on | off | profile <name> | profiles[/info]\n")


def handle_sessions_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /sessions command."""
    from ..session import (
        delete_all_sessions,
        delete_session,
        format_session_time_local,
        list_sessions,
    )

    args = args.strip()
    parts = args.split(None, 1) if args else []
    sub = parts[0] if parts else "list"
    sub_arg = parts[1].strip() if len(parts) > 1 else ""

    if sub in ("", "list"):
        sessions = list_sessions()
        if not sessions:
            console.print("\n[warning]No saved sessions found.[/warning]\n")
            return
        console.print(f"\n[info]Saved Sessions ({len(sessions)}):[/info]\n")
        for s in sessions:
            sid = s.get("session_id")
            short_id = sid[:8] if sid else s["id"]
            console.print(f"  • [cyan]{short_id}[/cyan]")
            if s.get("title"):
                console.print(f"    [bold]{s['title']}[/bold]")
            console.print(f"    Model: [dim]{s['model']}[/dim]  Messages: {s['message_count']}")
            if s.get("created_at"):
                created = format_session_time_local(s["created_at"])
                updated = format_session_time_local(s.get("updated_at"))
                console.print(f"    Created: {created}  Updated: {updated}")
            else:
                console.print(f"    Saved: {format_session_time_local(s['timestamp'])}")
            if s["active_skills"]:
                console.print(f"    Skills: {', '.join(s['active_skills'])}")
            console.print()
        console.print("[info]Usage:[/info] /sessions resume <id>  or  /sessions delete <id>  or  /sessions delete all\n")

    elif sub == "resume":
        resume_session(cli, sub_arg or None)

    elif sub == "delete":
        if sub_arg == "all":
            sessions = list_sessions()
            if not sessions:
                console.print("\n[warning]No saved sessions to delete.[/warning]\n")
                return
            console.print(f"\n[warning]Delete all {len(sessions)} session(s)? Press 1 to confirm, any other key to cancel.[/warning]")
            key = readchar.readkey()
            if key == "1":
                count = delete_all_sessions()
                console.print(f"\n[success]Deleted {count} session(s).[/success]\n")
            else:
                console.print("\n[info]Cancelled.[/info]\n")
            return
        if not sub_arg:
            console.print("\n[error]Usage: /sessions delete <session-id>  or  /sessions delete all[/error]\n")
            return
        if delete_session(sub_arg):
            console.print(f"\n[success]Session '{sub_arg}' deleted.[/success]\n")
        else:
            console.print(f"\n[warning]Session '{sub_arg}' not found.[/warning]\n")

    else:
        console.print(f"\n[error]Unknown subcommand: {sub}[/error]")
        console.print("[info]Available: /sessions list | /sessions resume <id> | /sessions delete <id> | /sessions delete all[/info]\n")


def resume_session(cli: AgentaoCLI, session_id: Optional[str] = None) -> None:
    """Load a previously saved session into the current agent."""
    import uuid as _uuid_mod
    from ..session import list_sessions, load_session

    sessions = list_sessions()
    if not sessions:
        console.print("\n[error]No saved sessions found.[/error]\n")
        return

    if session_id:
        match = next(
            (s for s in sessions
             if (s.get("session_id") or "").startswith(session_id)
             or s["id"].startswith(session_id)),
            None,
        )
        if not match:
            console.print(f"\n[error]Session '{session_id}' not found.[/error]\n")
            return
    else:
        match = sessions[0]  # newest

    try:
        messages, model, active_skills = load_session(match["id"])
    except FileNotFoundError as e:
        console.print(f"\n[error]Could not resume session: {e}[/error]\n")
        return

    cli.agent.messages = messages
    if model:
        try:
            cli.agent.set_model(model)
        except Exception:
            pass
    for skill_name in active_skills:
        try:
            cli.agent.skill_manager.activate_skill(skill_name, "Restored from session")
        except Exception:
            pass

    cli.current_session_id = match.get("session_id") or str(_uuid_mod.uuid4())
    cli.agent._session_id = cli.current_session_id
    cli.agent.tool_runner._session_id = cli.current_session_id

    # Restart replay so subsequent turns are recorded under the resumed session.
    try:
        cli.agent.end_replay()
        cli.agent.reload_replay_config()
        cli.agent.start_replay(cli.current_session_id)
    except Exception:
        pass

    sid_display = cli.current_session_id[:8]
    title_display = f": {match['title']}" if match.get("title") else ""
    msg_count = len(messages)
    console.print(f"\n[success]↩ Resuming session {sid_display}{title_display}[/success]")
    console.print(f"[dim]{msg_count} messages loaded.[/dim]")
    if model:
        console.print(f"[dim]Model: {model}[/dim]")
    if active_skills:
        console.print(f"[dim]Active skills: {', '.join(active_skills)}[/dim]")
    console.print()


def handle_tools_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /tools command."""
    args = args.strip()
    all_tools = cli.agent.tools.list_tools()

    if not args:
        console.print(f"\n[info]Registered Tools ({len(all_tools)}):[/info]\n")
        for tool in sorted(all_tools, key=lambda t: t.name):
            confirm = "  [warning]⚠ confirm[/warning]" if tool.requires_confirmation else ""
            console.print(f"  [cyan]{tool.name}[/cyan]{confirm}")
            console.print(f"    [dim]{tool.description}[/dim]")
        console.print()
        console.print("[dim]Use /tools <name> to see parameter schema.[/dim]\n")
    else:
        try:
            tool = cli.agent.tools.get(args)
        except KeyError:
            console.print(f"\n[error]Tool '{args}' not found.[/error]\n")
            return
        console.print(f"\n[info]{tool.name}[/info]")
        console.print(f"[dim]{tool.description}[/dim]")
        if tool.requires_confirmation:
            console.print("[warning]Requires user confirmation before execution[/warning]")
        console.print("\n[dim]Parameters schema:[/dim]")
        console.print(json.dumps(tool.parameters, indent=2, ensure_ascii=False))
        console.print()
