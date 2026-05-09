"""Feedback-driven rewrite flow + scope prompt for ``/crystallize``.

``_apply_feedback_to_draft`` is shared by both ``/crystallize feedback
<text>`` and ``/crystallize revise`` (which prompts for the text first
then calls into the same path). ``_prompt_scope`` collects the global-
vs-project decision before ``/crystallize create`` writes the SKILL.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import readchar
from rich.panel import Panel

from ..._globals import console
from ._evidence import render_crystallize_context

if TYPE_CHECKING:
    from ...app import AgentaoCLI


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


def _apply_feedback_to_draft(
    cli: AgentaoCLI,
    draft,
    feedback_text: str,
    *,
    wd: Path | None,
    sid: str,
    feedback_prompt_fn,
    feedback_system_prompt: str,
    extract_text_fn,
    append_feedback_fn,
    extract_name_fn,
    save_draft_fn,
) -> None:
    """Shared feedback-driven rewrite flow used by both ``feedback`` and ``revise``."""
    try:
        append_feedback_fn(draft, feedback_text, author="user")
    except ValueError as exc:
        console.print(f"\n[error]{exc}[/error]\n")
        return

    evidence_text = render_crystallize_context(draft.evidence)
    prior = draft.feedback_history[:-1]
    history_text = "\n".join(
        f"{i+1}. [{f.author}] {f.content}" for i, f in enumerate(prior)
    )
    user_content = feedback_prompt_fn(
        draft.content, evidence_text, feedback_text, history_text,
    )

    console.print("\n[dim]Rewriting draft with your feedback...[/dim]")
    try:
        response = cli.agent.llm.chat(
            messages=[
                {"role": "system", "content": feedback_system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=1200,
        )
        rewritten = extract_text_fn(response).strip()
    except Exception as exc:
        console.print(f"\n[error]LLM call failed: {exc}[/error]\n")
        try:
            save_draft_fn(draft, working_directory=wd, session_id=sid)
        except Exception:
            pass
        return

    if not rewritten or not rewritten.lstrip().startswith("---"):
        console.print(
            "\n[error]Feedback output is not a valid SKILL.md. "
            "Keeping previous draft (feedback recorded).[/error]\n"
        )
        # Persist the feedback history anyway so the user can see what they asked.
        try:
            save_draft_fn(draft, working_directory=wd, session_id=sid)
        except Exception:
            pass
        return

    new_name = extract_name_fn(rewritten) or draft.suggested_name
    draft.content = rewritten
    draft.suggested_name = new_name
    draft.source = "feedback"
    try:
        save_draft_fn(draft, working_directory=wd, session_id=sid)
    except Exception as exc:
        console.print(f"\n[error]Failed to save updated draft: {exc}[/error]\n")
        return

    console.print()
    console.print(Panel(
        rewritten,
        title="[cyan]Updated Skill Draft[/cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print(
        "[dim]Draft updated. Add more feedback with "
        "[cyan]/crystallize feedback <text>[/cyan] or save with "
        "[cyan]/crystallize create [name][/cyan].[/dim]\n"
    )
