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


def resume_session(
    cli: AgentaoCLI,
    session_id: Optional[str] = None,
    *,
    at_launch: bool = False,
) -> None:
    """Load a previously saved session into the current agent.

    ``at_launch`` distinguishes the two callers, which owe **different**
    lifecycle events (``docs/design/session-lifecycle-source-vs-codex.md`` §6.2):

    - ``agentao --resume`` (``at_launch=True``): no session has begun, so there
      is no ``SessionEnd`` to fire, and ``run_loop`` is about to dispatch the
      one ``SessionStart``. This path only leaves the one-shot marker that tells
      it to report ``resume`` instead of ``startup`` — and leaves it **only on
      success**, so a failed startup resume still reports ``startup`` for the
      real new session that begins anyway.
    - interactive ``/sessions resume`` (the default): the current session ends
      and a different one starts, so both events fire, in that order, carrying
      the **old** and **new** session ids respectively.

    Every early return below is a failed load, and dispatches nothing.
    """
    import uuid as _uuid_mod

    from ...embedding.sessions import list_sessions, load_session
    from ...runtime.model import purge_thinking_artifacts

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
    except (OSError, ValueError) as e:
        # ``OSError`` (``FileNotFoundError`` is one) and ``ValueError`` — the
        # same pair ``acp/session_load.py::resume_session_on_new`` catches, and
        # for the same reason. ``ValueError`` covers ``json.JSONDecodeError``
        # (a truncated or hand-edited file) *and* the not-an-object shape
        # ``load_session_record`` now normalizes into one; ``OSError`` covers
        # the unreadable file ``FileNotFoundError`` alone missed. It has to be
        # caught *here* rather than left to the caller, because the launch path
        # has no caller that survives it:
        # ``entrypoints.main`` wraps this in the fatal-error handler and exits 1,
        # which would turn one corrupt file into "``--resume`` cannot start the
        # CLI at all". The documented contract is that a failed startup resume
        # starts a normal session and reports ``startup``
        # (``docs/reference/configuration.md`` §11).
        console.print(f"\n[error]Could not resume session: {e}[/error]\n")
        return

    # The load succeeded, so the outgoing session is really ending. Fired here,
    # before any state is replaced, so the event still describes the session
    # being left. ``at_launch`` has no outgoing session, so it fires nothing.
    #
    # **Hooks only, symmetrically with the incoming side below.** The full
    # ``on_session_end`` would also persist the outgoing conversation, which
    # this command has never done and which is not free: ``save_session`` never
    # reuses a file for an existing session id, so every resume would write a
    # new one and ``_rotate_sessions`` would evict the oldest. Saving on resume
    # may well be the better product behaviour, but it is a separate decision
    # from reporting the event, and bundling it here would smuggle an eviction
    # site in behind a conformance fix.
    if not at_launch:
        from ..session import _dispatch_session_end_hooks
        _dispatch_session_end_hooks(cli, reason="resume")
        # Same reason ``_reset_session`` does it: the hook one-shot diagnostic
        # registry is keyed by session id, and the outgoing id is about to go
        # out of scope for good. Without this, every ``/sessions resume``
        # strands a bucket under a key nothing can reach for the life of the
        # process — the leak the reset path exists to avoid, on a boundary that
        # only became one when this command started reporting it.
        try:
            from ...plugins.hooks._diagnostics import clear_session
            clear_session(cli.current_session_id)
        except Exception:  # pragma: no cover - never block a resume
            pass

    cli.agent.messages = messages
    loaded_count = len(messages)
    # History was replaced wholesale; the Tier-1 token anchor describes the
    # prior conversation's prefix and must not survive into the resumed one.
    cli.agent.context_manager.invalidate_token_anchor()
    # Same reasoning applies to the saved conversation's thinking artifacts.
    # Because the persisted model is deliberately not restored (see below),
    # a resumed session is a model switch in everything but name — the
    # reasoning_content and thought_signatures on disk were minted by
    # whatever model that session ran, and are replayed to whatever model
    # this process is bound to.
    purge_thinking_artifacts(cli.agent.messages)
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

    if at_launch:
        # ``run_loop`` dispatches; see the docstring.
        cli._pending_session_start_source = "resume"
    else:
        # The incoming session begins here, but ``on_session_start`` is not
        # reused: it would re-derive the session id this function has already
        # resolved out of the loaded file. Its remaining steps are therefore
        # owed explicitly — the ids and the replay restart above, the memory
        # archive here, the hook dispatch last. **The archive is not optional
        # bookkeeping**: it advances ``MemoryManager._session_id``, and without
        # it the abandoned conversation's session summaries stay bound to the
        # resumed one and keep being injected into its prompts.
        try:
            cli.agent.memory_manager.archive_session()
        except Exception:
            pass
        from ..session import _dispatch_session_start_hooks
        _dispatch_session_start_hooks(cli, source="resume")

    sid_display = cli.current_session_id[:8]
    title_display = f": {match['title']}" if match.get("title") else ""
    console.print(f"\n[success]↩ Resuming session {sid_display}{title_display}[/success]")
    # ``loaded_count`` is snapshotted above the hook dispatch, not measured
    # here: ``cli.agent.messages`` is this very list object (assigned, not
    # copied), so a ``SessionStart`` hook's ``additionalContext`` lands in it
    # and would otherwise be reported as a loaded message.
    console.print(f"[dim]{loaded_count} messages loaded.[/dim]")
    current_model = cli.agent.get_current_model()
    console.print(f"[dim]Model: {current_model}[/dim]")
    if model and model != current_model:
        console.print(
            f"[dim](session was saved on {model}; keeping current model)[/dim]"
        )
    if active_skills:
        console.print(f"[dim]Active skills: {', '.join(active_skills)}[/dim]")
    console.print()
