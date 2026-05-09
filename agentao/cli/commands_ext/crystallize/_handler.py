"""``handle_crystallize_command`` — the ``/crystallize`` slash-command dispatcher.

Routes the seven subcommands (``suggest``, ``feedback``, ``revise``,
``refine``, ``create``, ``status``, ``clear``) through the helpers in
this package. Each branch is roughly an LLM call sandwiched between
draft load + draft save, with the ``create`` path also hitting the
filesystem to write a real SKILL.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.panel import Panel

from ..._globals import console
from ._evidence import collect_crystallize_evidence, render_crystallize_context
from ._feedback import _apply_feedback_to_draft, _prompt_scope
from ._helpers import _collect_session_content, _sanitize_skill_name
from ._suggest import _suggest_draft_or_skip

if TYPE_CHECKING:
    from ...app import AgentaoCLI


def handle_crystallize_command(cli: AgentaoCLI, args: str = "") -> None:
    """Handle /crystallize [suggest|feedback|revise|refine|create [name]|status|clear] commands."""
    from ....memory.crystallizer import (
        FEEDBACK_SYSTEM_PROMPT,
        REFINE_SYSTEM_PROMPT,
        SkillCrystallizer,
        _extract_text,
        feedback_prompt,
        load_skill_creator_guidance,
        refine_prompt,
    )
    from ....skills.drafts import (
        append_skill_feedback,
        clear_skill_draft,
        extract_skill_name,
        load_skill_draft,
        new_draft,
        replace_skill_name,
        save_skill_draft,
        summarize_draft_status,
    )

    parts = args.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "suggest"
    sub_arg = parts[1].strip() if len(parts) > 1 else ""

    valid = {"suggest", "feedback", "revise", "refine", "create", "status", "clear"}
    if subcommand not in valid:
        console.print(
            "\n[error]Usage: /crystallize [suggest|feedback <text>|revise|refine|"
            "create [name]|status|clear][/error]\n"
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
        info = summarize_draft_status(draft)
        console.print("\n[info]Pending skill draft:[/info]")
        console.print(f"  name: [cyan]{info['name'] or '(unknown)'}[/cyan]")
        console.print(f"  source: {info['source']}")
        console.print(f"  refined_with: {info['refined_with'] or '(none)'}")
        console.print(f"  updated_at: {info['updated_at']}")
        console.print(f"  feedback_count: {info['feedback_count']}")
        console.print(f"  tool_call_count: {info['tool_call_count']}")
        console.print(f"  tool_result_count: {info['tool_result_count']}")
        console.print(f"  workflow_step_count: {info['workflow_step_count']}")
        console.print(f"  key_file_count: {info['key_file_count']}\n")
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
        evidence = collect_crystallize_evidence(cli)
        evidence_text = render_crystallize_context(evidence)
        draft_text = _suggest_draft_or_skip(cli, session_content, evidence_text)
        if draft_text is None:
            return

        suggested_name = extract_skill_name(draft_text) or ""
        draft = new_draft(
            content=draft_text,
            suggested_name=suggested_name,
            session_id=sid,
            source="suggest",
            evidence=evidence,
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
            console.print("[dim]Use /crystallize feedback <text> to revise it.[/dim]")
            console.print("[dim]Use /crystallize refine to improve it with skill-creator.[/dim]")
            console.print("[dim]Use /crystallize create [name] to save it.[/dim]\n")
        else:
            console.print(f"[warning]Draft could not be persisted: {save_error}[/warning]")
            console.print("[dim]Review the draft above and use /crystallize create [name] to save it directly.[/dim]\n")
        return

    # ---------- /crystallize feedback <text> ----------
    if subcommand == "feedback":
        if not sub_arg:
            console.print(
                "\n[error]Usage: /crystallize feedback <text>[/error]\n"
            )
            return
        draft = load_skill_draft(working_directory=wd, session_id=sid)
        if draft is None:
            console.print(
                "\n[warning]No pending skill draft. Run /crystallize suggest first.[/warning]\n"
            )
            return
        _apply_feedback_to_draft(
            cli, draft, sub_arg, wd=wd, sid=sid,
            feedback_prompt_fn=feedback_prompt,
            feedback_system_prompt=FEEDBACK_SYSTEM_PROMPT,
            extract_text_fn=_extract_text,
            append_feedback_fn=append_skill_feedback,
            extract_name_fn=extract_skill_name,
            save_draft_fn=save_skill_draft,
        )
        return

    # ---------- /crystallize revise ----------
    if subcommand == "revise":
        draft = load_skill_draft(working_directory=wd, session_id=sid)
        if draft is None:
            console.print(
                "\n[warning]No pending skill draft. Run /crystallize suggest first.[/warning]\n"
            )
            return
        try:
            feedback_text = console.input(
                "[cyan]Feedback[/cyan] (what should change?): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[warning]Cancelled.[/warning]\n")
            return
        if not feedback_text:
            console.print("\n[warning]No feedback provided — cancelled.[/warning]\n")
            return
        _apply_feedback_to_draft(
            cli, draft, feedback_text, wd=wd, sid=sid,
            feedback_prompt_fn=feedback_prompt,
            feedback_system_prompt=FEEDBACK_SYSTEM_PROMPT,
            extract_text_fn=_extract_text,
            append_feedback_fn=append_skill_feedback,
            extract_name_fn=extract_skill_name,
            save_draft_fn=save_skill_draft,
        )
        return

    # ---------- /crystallize refine ----------
    if subcommand == "refine":
        draft = load_skill_draft(working_directory=wd, session_id=sid)
        if draft is None:
            console.print("\n[warning]No pending skill draft. Run /crystallize suggest first.[/warning]\n")
            return

        session_content = _collect_session_content(cli)
        guidance = load_skill_creator_guidance()
        evidence_text = render_crystallize_context(draft.evidence)
        console.print("\n[dim]Refining draft with skill-creator guidance...[/dim]")
        try:
            response = cli.agent.llm.chat(
                messages=[
                    {"role": "system", "content": REFINE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": refine_prompt(
                            draft.content, session_content, guidance, evidence_text,
                        ),
                    },
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
        evidence = collect_crystallize_evidence(cli)
        evidence_text = render_crystallize_context(evidence)
        draft_text = _suggest_draft_or_skip(cli, session_content, evidence_text)
        if draft_text is None:
            return
        draft = new_draft(
            content=draft_text,
            suggested_name=extract_skill_name(draft_text) or "",
            session_id=sid,
            source="suggest",
            evidence=evidence,
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
