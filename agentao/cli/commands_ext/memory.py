"""`/memory` slash command — list/search/tag/delete/clear/review memory entries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.prompt import Confirm

from .._globals import console
from .._utils import _display_layered_entries

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def show_memories(cli: AgentaoCLI, subcommand: str = "", arg: str = "") -> None:
    """Show saved memories."""
    mgr = cli.agent.memory_manager

    def _print_entry(e) -> None:
        console.print(f"  • [cyan]{e.title}[/cyan] [{e.scope}]: {e.content[:120]}")
        if e.tags:
            console.print(f"    Tags: {', '.join(e.tags)}")
        console.print(f"    Updated: {e.updated_at}")
        console.print()

    if subcommand in ["", "list"]:
        entries = mgr.get_all_entries()
        if not entries:
            console.print("\n[warning]No memories saved yet.[/warning]\n")
            return
        console.print(f"\n[info]Saved Memories ({len(entries)} total):[/info]\n")
        for e in entries:
            _print_entry(e)
        all_tags: dict = {}
        for e in entries:
            for tag in e.tags:
                all_tags[tag] = all_tags.get(tag, 0) + 1
        if all_tags:
            console.print("[info]Tag Summary:[/info]")
            for tag, count in sorted(all_tags.items(), key=lambda x: -x[1]):
                console.print(f"  [dim]#{tag}[/dim] ({count})")
            console.print()

    elif subcommand == "search":
        if not arg:
            console.print("\n[error]Usage: /memory search <query>[/error]\n")
            return
        results = mgr.search(arg)
        if not results:
            console.print(f"\n[warning]No memories found matching '{arg}'[/warning]\n")
            return
        console.print(f"\n[info]Found {len(results)} memory(ies) matching '{arg}':[/info]\n")
        for e in results:
            _print_entry(e)

    elif subcommand == "tag":
        if not arg:
            console.print("\n[error]Usage: /memory tag <tag_name>[/error]\n")
            return
        results = mgr.filter_by_tag(arg)
        if not results:
            console.print(f"\n[warning]No memories found with tag '{arg}'[/warning]\n")
            return
        console.print(f"\n[info]Found {len(results)} memory(ies) with tag '{arg}':[/info]\n")
        for e in results:
            _print_entry(e)

    elif subcommand == "delete":
        if not arg:
            console.print("\n[error]Usage: /memory delete <key>[/error]\n")
            return
        count = mgr.delete_by_title(arg)
        if not count:
            for e in mgr.get_all_entries():
                if e.key_normalized == arg or e.key_normalized == arg.lower().replace(" ", "_"):
                    if mgr.delete(e.id):
                        count += 1
        if count:
            # Step 6 replay event — CLI-driven soft-deletes show up alongside
            # tool-driven MEMORY_WRITE events so a reader can reconstruct
            # memory mutations from either channel.
            try:
                from agentao.transport import AgentEvent, EventType
                cli.agent.transport.emit(AgentEvent(EventType.MEMORY_DELETE, {
                    "key": arg,
                    "deleted_count": count,
                    "cause": "cli",
                }))
            except Exception:
                pass
            console.print(f"\n[success]Successfully deleted memory: {arg}[/success]\n")
        else:
            console.print(f"\n[warning]Memory not found: {arg}[/warning]\n")

    elif subcommand == "clear":
        if Confirm.ask("\n[warning]Are you sure you want to delete ALL memories? This cannot be undone.[/warning]", default=False):
            count = mgr.clear()
            summary_count = mgr.clear_all_session_summaries()
            try:
                from agentao.transport import AgentEvent, EventType
                cli.agent.transport.emit(AgentEvent(EventType.MEMORY_CLEARED, {
                    "memories_cleared": count,
                    "session_summaries_cleared": summary_count,
                    "cause": "cli",
                }))
            except Exception:
                pass
            console.print(f"\n[success]Successfully cleared {count} memory(ies)[/success]\n")
        else:
            console.print("\n[info]Cancelled.[/info]\n")

    elif subcommand == "user":
        entries = mgr.get_all_entries(scope="user")
        _display_layered_entries(entries, "[Profile Memory]", console)

    elif subcommand == "project":
        entries = mgr.get_all_entries(scope="project")
        _display_layered_entries(entries, "[Project Memory]", console)

    elif subcommand == "session":
        summaries = mgr.get_recent_session_summaries(limit=10)
        if summaries:
            combined = "\n\n---\n\n".join(s.summary_text for s in reversed(summaries))
            console.print(f"\n[info]Session Memory ({len(combined)} chars, {len(summaries)} summaries):[/info]\n")
            console.print(combined[-2000:] if len(combined) > 2000 else combined)
        else:
            console.print("\n[warning]No active session summary.[/warning]\n")

    elif subcommand == "crystallize":
        items = mgr.crystallize_user_messages(cli.agent.messages)
        if not items:
            console.print("\n[warning]No crystallization candidates found in current conversation.[/warning]\n")
            return
        console.print(f"\n[info]Added/updated {len(items)} review queue item(s):[/info]\n")
        for it in items:
            console.print(f"  • [cyan]{it.title}[/cyan] [{it.type}, {it.scope}] occ={it.occurrences}")
            if it.evidence:
                console.print(f"    [dim]Evidence:[/dim] {it.evidence[:120]}")
        console.print()

    elif subcommand == "review":
        parts = arg.split(maxsplit=1) if arg else [""]
        action = parts[0]
        target = parts[1] if len(parts) > 1 else ""
        if not action:
            items = mgr.list_review_items()
            if not items:
                console.print("\n[warning]Review queue is empty.[/warning]\n")
                return
            console.print(f"\n[info]Pending review items ({len(items)}):[/info]\n")
            for it in items:
                console.print(f"  [{it.id}] [cyan]{it.title}[/cyan] {it.type}/{it.scope} occ={it.occurrences}")
                if it.evidence:
                    console.print(f"      [dim]{it.evidence[:120]}[/dim]")
            console.print("\n  Approve: /memory review approve <id>")
            console.print("  Reject:  /memory review reject <id>\n")
        elif action == "approve" and target:
            rec = mgr.approve_review_item(target)
            if rec:
                console.print(f"\n[success]Approved → memory '{rec.title}' (source=crystallized)[/success]\n")
            else:
                console.print(f"\n[warning]No pending review item with id '{target}'[/warning]\n")
        elif action == "reject" and target:
            ok = mgr.reject_review_item(target)
            if ok:
                console.print(f"\n[success]Rejected[/success]\n")
            else:
                console.print(f"\n[warning]No pending review item with id '{target}'[/warning]\n")
        else:
            console.print("\n[error]Usage: /memory review [approve|reject <id>][/error]\n")

    elif subcommand == "status":
        user_entries = mgr.get_all_entries(scope="user")
        proj_entries = mgr.get_all_entries(scope="project")
        session_summaries = mgr.get_recent_session_summaries(limit=100)
        retriever = getattr(cli.agent, 'memory_retriever', None)
        recall_count = retriever._recall_count if retriever else 0
        error_count = retriever._error_count if retriever else 0
        last_error = retriever._last_error if retriever else ""
        stable_chars = getattr(cli.agent, '_stable_block_chars', 0)
        latest_summary = session_summaries[0].summary_text if session_summaries else ""
        session_chars = len(latest_summary)
        console.print("\n[info]Memory Status:[/info]")
        console.print(f"  Profile  (user):        {len(user_entries)} entries")
        console.print(f"  Project:                {len(proj_entries)} entries")
        console.print(f"  Session summaries:      {len(session_summaries)}")
        console.print(f"  Recall hits (session):  {recall_count}")
        console.print(f"  Recall errors (session):{error_count}")
        if last_error:
            console.print(f"  Last recall error:      {last_error}")
        console.print(f"  Stable block size:      {stable_chars} chars")
        console.print(f"  Latest session summary: {session_chars} chars\n")

    else:
        console.print(f"\n[error]Unknown subcommand: {subcommand}[/error]")
        console.print("[info]Available subcommands: list, search, tag, delete, clear, user, project, session, status, crystallize, review[/info]\n")
