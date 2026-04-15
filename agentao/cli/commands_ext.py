"""Extended slash command handlers (heavier dependencies)."""

from __future__ import annotations

import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

import readchar
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from ._globals import console
from ._utils import _display_layered_entries

if TYPE_CHECKING:
    from .app import AgentaoCLI


def _collect_session_content(cli: AgentaoCLI) -> str:
    """Merge compacted session summaries + live conversation turns."""
    session_content = ""
    summaries = cli.agent.memory_manager.get_recent_session_summaries(limit=5)
    if summaries:
        session_content = "\n\n---\n\n".join(s.summary_text for s in reversed(summaries))

    live_parts = []
    for msg in cli.agent.messages:
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if content:
            live_parts.append(f"{role.capitalize()}: {content}")
    if live_parts:
        live_section = "\n".join(live_parts)
        session_content = (session_content + "\n\n" + live_section).strip()
    return session_content


def _sanitize_skill_name(raw: str) -> str:
    import re as _re
    return _re.sub(r'[^a-z0-9-]', '-', (raw or "").lower()).strip('-')


def _prompt_scope() -> str | None:
    console.print("\n[dim]Scope: [cyan]g[/cyan]lobal (~/.agentao/skills/) or [cyan]p[/cyan]roject (.agentao/skills/)?[/dim]")
    console.print("[dim]Press g or p[/dim]", end=" ")
    while True:
        key = readchar.readkey()
        if key == "g":
            console.print("\n")
            return "global"
        if key == "p":
            console.print("\n")
            return "project"
        if key in (readchar.key.ESC, "\x03"):
            console.print("\n[warning]Cancelled.[/warning]\n")
            return None


def handle_crystallize_command(cli: AgentaoCLI, args: str = "") -> None:
    """Handle /crystallize [suggest|refine|create [name]|status|clear] commands."""
    from ..memory.crystallizer import (
        SkillCrystallizer,
        SUGGEST_SYSTEM_PROMPT,
        suggest_prompt,
        REFINE_SYSTEM_PROMPT,
        refine_prompt,
        load_skill_creator_guidance,
        _extract_text,
    )
    from ..skills.drafts import (
        clear_skill_draft,
        extract_skill_name,
        load_skill_draft,
        new_draft,
        replace_skill_name,
        save_skill_draft,
    )

    parts = args.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "suggest"
    sub_arg = parts[1].strip() if len(parts) > 1 else ""

    valid = {"suggest", "refine", "create", "status", "clear"}
    if subcommand not in valid:
        console.print(
            "\n[error]Usage: /crystallize [suggest|refine|create [name]|status|clear][/error]\n"
        )
        return

    # Scope drafts to the agent's explicit working directory (ACP/background
    # sessions may run with cwd != project root), and key them by session_id
    # so concurrent sessions in the same repo don't clobber each other.
    wd = getattr(cli.agent, "working_directory", None)
    sid = getattr(cli.agent, "_session_id", None) or ""

    # ---------- /crystallize status ----------
    if subcommand == "status":
        draft = load_skill_draft(working_directory=wd, session_id=sid)
        if draft is None:
            console.print("\n[dim]No pending skill draft.[/dim]\n")
            return
        console.print("\n[info]Pending skill draft:[/info]")
        console.print(f"  name: [cyan]{draft.suggested_name or '(unknown)'}[/cyan]")
        console.print(f"  source: {draft.source}")
        console.print(f"  refined_with: {draft.refined_with or '(none)'}")
        console.print(f"  updated_at: {draft.updated_at}\n")
        return

    # ---------- /crystallize clear ----------
    if subcommand == "clear":
        if clear_skill_draft(working_directory=wd, session_id=sid):
            console.print("\n[success]Pending skill draft cleared.[/success]\n")
        else:
            console.print("\n[dim]No pending skill draft.[/dim]\n")
        return

    # ---------- /crystallize suggest ----------
    if subcommand == "suggest":
        session_content = _collect_session_content(cli)
        if not session_content:
            console.print("\n[warning]No session content found. Start a conversation first.[/warning]\n")
            return
        console.print("\n[dim]Analyzing session to generate skill draft...[/dim]")
        try:
            response = cli.agent.llm.chat(
                messages=[
                    {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
                    {"role": "user", "content": suggest_prompt(session_content)},
                ],
                max_tokens=800,
            )
            draft_text = _extract_text(response).strip()
        except Exception as e:
            console.print(f"\n[error]LLM call failed: {e}[/error]\n")
            return

        if not draft_text or draft_text == "NO_PATTERN_FOUND":
            console.print(
                "\n[warning]No clear repeatable skill pattern found in the current session.[/warning]\n"
            )
            return

        suggested_name = extract_skill_name(draft_text) or ""
        draft = new_draft(
            content=draft_text,
            suggested_name=suggested_name,
            session_id=sid,
            source="suggest",
        )
        save_error: Exception | None = None
        try:
            save_skill_draft(draft, working_directory=wd, session_id=sid)
        except Exception as e:
            save_error = e

        console.print()
        console.print(Panel(draft_text, title="[cyan]Skill Draft[/cyan]", border_style="cyan", padding=(1, 2)))
        if save_error is None:
            console.print("[dim]Draft saved.[/dim]")
            console.print("[dim]Use /crystallize refine to improve it with skill-creator.[/dim]")
            console.print("[dim]Use /crystallize create [name] to save it.[/dim]\n")
        else:
            console.print(f"[warning]Draft could not be persisted: {save_error}[/warning]")
            console.print("[dim]Review the draft above and use /crystallize create [name] to save it directly.[/dim]\n")
        return

    # ---------- /crystallize refine ----------
    if subcommand == "refine":
        draft = load_skill_draft(working_directory=wd, session_id=sid)
        if draft is None:
            console.print("\n[warning]No pending skill draft. Run /crystallize suggest first.[/warning]\n")
            return

        session_content = _collect_session_content(cli)
        guidance = load_skill_creator_guidance()
        console.print("\n[dim]Refining draft with skill-creator guidance...[/dim]")
        try:
            response = cli.agent.llm.chat(
                messages=[
                    {"role": "system", "content": REFINE_SYSTEM_PROMPT},
                    {"role": "user", "content": refine_prompt(draft.content, session_content, guidance)},
                ],
                max_tokens=1200,
            )
            refined = _extract_text(response).strip()
        except Exception as e:
            console.print(f"\n[error]LLM call failed: {e}[/error]\n")
            return

        if not refined or not refined.lstrip().startswith("---"):
            console.print("\n[error]Refine output is not a valid SKILL.md. Keeping previous draft.[/error]\n")
            return

        refined_name = extract_skill_name(refined) or draft.suggested_name
        draft.content = refined
        draft.suggested_name = refined_name
        draft.refined_with = "skill-creator"
        try:
            save_skill_draft(draft, working_directory=wd, session_id=sid)
        except Exception as e:
            console.print(f"\n[error]Failed to save refined draft: {e}[/error]\n")
            return

        console.print()
        console.print(Panel(refined, title="[cyan]Refined Skill Draft[/cyan]", border_style="cyan", padding=(1, 2)))
        console.print("[dim]Refined draft saved. Use /crystallize create [name] to persist it.[/dim]\n")
        return

    # ---------- /crystallize create ----------
    # Fall back to generating a draft on-the-fly when none has been
    # saved: pre-patch, ``/crystallize create [name]`` generated from
    # the current session and wrote immediately. Preserve that
    # one-shot path so existing scripts that call ``create`` directly
    # still work.
    draft = load_skill_draft(working_directory=wd, session_id=sid)
    if draft is None:
        session_content = _collect_session_content(cli)
        if not session_content:
            console.print("\n[warning]No session content found. Start a conversation first.[/warning]\n")
            return
        console.print("\n[dim]Analyzing session to generate skill draft...[/dim]")
        try:
            response = cli.agent.llm.chat(
                messages=[
                    {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
                    {"role": "user", "content": suggest_prompt(session_content)},
                ],
                max_tokens=800,
            )
            draft_text = _extract_text(response).strip()
        except Exception as e:
            console.print(f"\n[error]LLM call failed: {e}[/error]\n")
            return
        if not draft_text or draft_text == "NO_PATTERN_FOUND":
            console.print(
                "\n[warning]No clear repeatable skill pattern found in the current session.[/warning]\n"
            )
            return
        draft = new_draft(
            content=draft_text,
            suggested_name=extract_skill_name(draft_text) or "",
            session_id=sid,
            source="suggest",
        )
        # Persisting the draft is only needed to resume across sessions.
        # A read-only project directory must not block one-shot creates
        # whose final target could still be the writable global skills
        # dir (~/.agentao/skills).
        try:
            save_skill_draft(draft, working_directory=wd, session_id=sid)
        except Exception as e:
            console.print(
                f"\n[warning]Could not persist draft ({e}); continuing "
                "with in-memory draft.[/warning]"
            )
        console.print()
        console.print(Panel(draft_text, title="[cyan]Skill Draft[/cyan]", border_style="cyan", padding=(1, 2)))

    name_arg = sub_arg or draft.suggested_name
    if not name_arg:
        name_arg = console.input("[cyan]Skill directory name[/cyan] (e.g. python-testing): ").strip()
        if not name_arg:
            console.print("[warning]Cancelled — no name provided.[/warning]\n")
            return
    name = _sanitize_skill_name(name_arg)
    if not name:
        console.print("[warning]Invalid skill name.[/warning]\n")
        return

    # If user supplied an explicit name (via arg or prompt) different from draft, rewrite frontmatter.
    # Drafts without YAML frontmatter are still salvageable — persist the
    # raw content with a warning instead of abandoning the user mid-flow.
    content = draft.content
    if name != (extract_skill_name(content) or ""):
        try:
            content = replace_skill_name(content, name)
        except ValueError:
            console.print(
                "[warning]Draft has no YAML frontmatter; saving raw content. "
                "Add `name:` / `description:` manually if needed.[/warning]"
            )

    scope = _prompt_scope()
    if scope is None:
        return

    crystallizer = SkillCrystallizer()
    try:
        project_root = Path(wd) if wd else None
        target = crystallizer.create(
            name, scope, content, project_root=project_root,
        )
    except Exception as e:
        console.print(f"\n[error]Failed to write skill: {e}[/error]\n")
        return

    try:
        count = cli.agent.skill_manager.reload_skills()
    except Exception:
        count = None

    clear_skill_draft(working_directory=wd, session_id=sid)

    console.print(f"\n[success]Skill saved to:[/success] [cyan]{target}[/cyan]")
    if count is not None:
        console.print(f"[dim]Skills reloaded ({count} available). Activate with /skills activate {name}[/dim]\n")
    else:
        console.print(f"[dim]Activate with /skills activate {name}[/dim]\n")


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
            console.print(f"\n[success]Successfully deleted memory: {arg}[/success]\n")
        else:
            console.print(f"\n[warning]Memory not found: {arg}[/warning]\n")

    elif subcommand == "clear":
        if Confirm.ask("\n[warning]Are you sure you want to delete ALL memories? This cannot be undone.[/warning]", default=False):
            count = mgr.clear()
            mgr.clear_all_session_summaries()
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


def _show_agents_dashboard(cli: AgentaoCLI) -> None:
    """Render a live auto-refreshing table of all background agents."""
    import time as _time
    from rich.live import Live
    from rich.table import Table
    from rich import box as rich_box
    from rich.text import Text
    from ..agents.tools import list_bg_tasks

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
    from ..agents.tools import list_bg_tasks, get_bg_task
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
        from ..agents.tools import _cancel_bg_task
        msg = _cancel_bg_task(agent_id)
        console.print(f"\n{msg}\n")
        return

    if sub == "delete":
        agent_id = rest.strip()
        if not agent_id:
            console.print("\n[error]Usage: /agent delete <agent-id>[/error]\n")
            return
        from ..agents.tools import _delete_bg_task
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


# ---------------------------------------------------------------------------
# /acp command (Issue 06)
# ---------------------------------------------------------------------------


def _ensure_acp_manager(cli: AgentaoCLI):
    """Lazy-initialize the ACP manager on first /acp usage.

    Returns the manager (may be ``None`` if no config found).
    """
    if cli._acp_manager is not None:
        return cli._acp_manager

    try:
        from ..acp_client import ACPManager
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

    running = sum(1 for s in statuses if s["state"] not in ("configured", "stopped", "failed"))
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
        color = _STATE_COLORS.get(s["state"], "dim")
        desc = f"  [dim]{s['description']}[/dim]" if s.get("description") else ""
        pid_str = f" pid={s['pid']}" if s["pid"] else ""
        err = f"  [red]{s['last_error']}[/red]" if s.get("last_error") else ""
        interact_str = ""
        if s.get("interactions_pending"):
            interact_str = f"  [magenta]⏳ {s['interactions_pending']} interaction(s)[/magenta]"
        console.print(
            f"  [{color}]●[/{color}] [cyan]{s['name']}[/cyan] "
            f"[{color}]{s['state']}[/{color}]{pid_str}{desc}{interact_str}{err}"
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
    from ..acp_client.interaction import InteractionKind

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
            from ..acp_client.render import flush_to_console
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
                    from ..acp_client.render import flush_to_console
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
        import time as _time
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


