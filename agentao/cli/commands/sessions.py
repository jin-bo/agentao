"""``/sessions`` and ``resume_session`` — saved-session management.

``handle_sessions_command`` dispatches list/resume/delete; ``resume_session``
is also exported separately because ``cli.entrypoints`` calls it directly
when the user passes ``--resume`` on launch (no slash-command parse).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import readchar

from .._globals import console, split_subcommand, unknown_subcommand

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def handle_sessions_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /sessions command."""
    from ...embedding.sessions import (
        delete_all_sessions,
        delete_session,
        format_session_time_local,
        list_sessions,
    )

    project_root = cli.agent.working_directory

    sub, sub_arg = split_subcommand(args, default="list")

    if sub in ("", "list"):
        sessions = list_sessions(project_root=project_root)
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
            sessions = list_sessions(project_root=project_root)
            if not sessions:
                console.print("\n[warning]No saved sessions to delete.[/warning]\n")
                return
            console.print(f"\n[warning]Delete all {len(sessions)} session(s)? Press 1 to confirm, any other key to cancel.[/warning]")
            key = readchar.readkey()
            if key == "1":
                count = delete_all_sessions(project_root=project_root)
                console.print(f"\n[success]Deleted {count} session(s).[/success]\n")
            else:
                console.print("\n[info]Cancelled.[/info]\n")
            return
        if not sub_arg:
            console.print("\n[error]Usage: /sessions delete <session-id>  or  /sessions delete all[/error]\n")
            return
        if delete_session(sub_arg, project_root=project_root):
            console.print(f"\n[success]Session '{sub_arg}' deleted.[/success]\n")
        else:
            console.print(f"\n[warning]Session '{sub_arg}' not found.[/warning]\n")

    else:
        console.print(unknown_subcommand(sub))
        console.print("[info]Available: /sessions list | /sessions resume <id> | /sessions delete <id> | /sessions delete all[/info]\n")


def resume_session(cli: AgentaoCLI, session_id: Optional[str] = None) -> None:
    """Load a previously saved session into the current agent."""
    import uuid as _uuid_mod

    from ...embedding.sessions import list_sessions, load_session

    project_root = cli.agent.working_directory
    sessions = list_sessions(project_root=project_root)
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
        messages, model, active_skills = load_session(match["id"], project_root=project_root)
    except FileNotFoundError as e:
        console.print(f"\n[error]Could not resume session: {e}[/error]\n")
        return

    cli.agent.messages = messages
    # History was replaced wholesale; the Tier-1 token anchor describes the
    # prior conversation's prefix and must not survive into the resumed one.
    cli.agent.context_manager.invalidate_token_anchor()
    # Intentionally do NOT restore the persisted model. A session stores only
    # the model *name*, not its provider (api_key / base_url never touch disk).
    # Re-binding the name onto whatever provider the current process happens to
    # use yields an inconsistent (provider, model) pair — e.g. a model saved
    # under provider A that does not exist on the now-current provider B, which
    # only fails on the next LLM call. Keep the current process's already-
    # consistent (provider, model) and surface the saved name for reference.
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
    current_model = cli.agent.get_current_model()
    console.print(f"[dim]Model: {current_model}[/dim]")
    if model and model != current_model:
        console.print(
            f"[dim](session was saved on {model}; keeping current model)[/dim]"
        )
    if active_skills:
        console.print(f"[dim]Active skills: {', '.join(active_skills)}[/dim]")
    console.print()
