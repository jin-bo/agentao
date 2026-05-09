"""Suggest-phase rendering + LLM-call wrapper for ``/crystallize``.

``render_available_skills_summary`` is reused by the system prompt so
the model sees the existing skill catalogue and can avoid duplicate
proposals. ``_suggest_draft_or_skip`` runs the LLM and either returns
a draft or surfaces a skip / error message — shared by both
``/crystallize suggest`` and the ``/crystallize create`` fallback path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List

from ..._globals import console

if TYPE_CHECKING:
    from ...app import AgentaoCLI


_AVAILABLE_SKILLS_BUDGET = 2000


def render_available_skills_summary(cli: AgentaoCLI) -> str:
    """Render an `available_skills_text` block for the suggest prompt.

    Sort order (cheapest-deduplication first):
      1. Currently active skills (most likely to overlap with the session)
      2. Other skills, by file mtime descending (recently installed/updated)
      3. Alphabetical as final tiebreaker

    Truncated to ``_AVAILABLE_SKILLS_BUDGET`` chars; if truncation drops
    any skills, a final hint line tells the model its view is partial so
    it stays conservative on the duplication gate.
    """
    sm = getattr(cli.agent, "skill_manager", None)
    if sm is None:
        return ""
    names = sm.list_available_skills()
    if not names:
        return ""
    active = set(sm.get_active_skills().keys())

    def _mtime(meta: dict) -> float:
        path = meta.get("path") or ""
        try:
            return Path(path).stat().st_mtime if path else 0.0
        except OSError:
            return 0.0

    visible: List[tuple] = []
    for name in names:
        meta = sm.get_skill_info(name) or {}
        visible.append((name, meta))
    visible.sort(key=lambda item: (
        0 if item[0] in active else 1,
        -_mtime(item[1]),
        item[0],
    ))

    total = len(visible)

    def _hint(n: int) -> str:
        return (
            f"(showing top {n} of {total} installed skills; "
            f"the model may not see all of them — be conservative on duplication)"
        )

    # Reserve worst-case hint room so a render+hint always fits in the
    # budget; rendered ≤ total so digit width is bounded.
    hint_reserve = len(_hint(total)) + 1

    lines: List[str] = []
    used = 0
    rendered = 0
    for name, meta in visible:
        desc = (meta.get("description") or "").strip().splitlines()
        first = desc[0].strip() if desc else ""
        if len(first) > 200:
            first = first[:197].rstrip() + "..."
        line = f"- {name} — {first}" if first else f"- {name}"
        if used + len(line) + 1 + hint_reserve > _AVAILABLE_SKILLS_BUDGET and rendered > 0:
            break
        lines.append(line)
        used += len(line) + 1
        rendered += 1

    if rendered < total:
        lines.append(_hint(rendered))
    return "\n".join(lines)


def _render_noop_skip(parsed) -> None:
    """Print a user-facing skip line for a parsed NO-OP signal.

    ``parsed`` is the return value of :func:`parse_noop_skip`, or
    ``(None, "")`` for "empty draft / no signal".
    """
    gate_id, reason = parsed
    if gate_id is None:
        console.print(
            "\n[warning]No clear repeatable skill pattern found in the current session.[/warning]\n"
        )
        return
    suffix = f": {reason}" if reason else "."
    console.print(f"\n[warning]Skipped — {gate_id}[/warning]{suffix}\n")


def _suggest_draft_or_skip(cli: AgentaoCLI, session_content: str, evidence_text: str):
    """Run the suggest LLM call and either return the draft text, or print
    the skip / error message and return None.

    Shared by ``/crystallize suggest`` and the ``/crystallize create``
    fallback path that has no pending draft.
    """
    from ....memory.crystallizer import (
        SUGGEST_SYSTEM_PROMPT,
        _extract_text,
        parse_noop_skip,
        suggest_prompt,
    )

    console.print("\n[dim]Analyzing session to generate skill draft...[/dim]")
    try:
        response = cli.agent.llm.chat(
            messages=[
                {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": suggest_prompt(
                        session_content,
                        evidence_text,
                        render_available_skills_summary(cli),
                    ),
                },
            ],
            max_tokens=800,
        )
        draft_text = _extract_text(response).strip()
    except Exception as e:
        console.print(f"\n[error]LLM call failed: {e}[/error]\n")
        return None

    if not draft_text:
        _render_noop_skip((None, ""))
        return None
    skip = parse_noop_skip(draft_text)
    if skip is not None:
        _render_noop_skip(skip)
        return None
    return draft_text
