"""Evidence collection + rendering for ``/crystallize``.

``collect_crystallize_evidence`` walks the live conversation history and
produces a structured :class:`SkillEvidence`. ``render_crystallize_context``
turns that into a compact prompt block. Both are public — tests import
them directly to verify the projection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._helpers import (
    _PATHY_ARG_KEYS,
    _clip,
    _detect_key_paths,
    _first_sentences,
    _message_text,
    _parse_tool_args,
    _short_args_summary,
)

if TYPE_CHECKING:
    from ...app import AgentaoCLI


def collect_crystallize_evidence(cli: AgentaoCLI):
    """Walk the current conversation history and extract structured evidence.

    Returns a :class:`SkillEvidence` populated from user messages, assistant
    conclusions (non tool-call assistant content), ``assistant.tool_calls``,
    and ``role="tool"`` result messages. Long tool outputs are truncated so
    this can safely be embedded in an LLM prompt.
    """
    from ....skills.drafts import SkillEvidence

    user_goals: list[str] = []
    assistant_conclusions: list[str] = []
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    key_files_seen: list[str] = []
    workflow_steps: list[str] = []
    outcome_signals: list[str] = []

    def _add_file(p: str) -> None:
        if p and p not in key_files_seen:
            key_files_seen.append(p)

    messages = getattr(cli.agent, "messages", []) or []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if role == "user":
            text = _message_text(msg.get("content"))
            if not text:
                continue
            if text.startswith("[PIN]"):
                text = text[len("[PIN]"):].lstrip()
            first = _first_sentences(text, max_chars=200)
            if first and first not in user_goals:
                user_goals.append(first)
        elif role == "assistant":
            text = _message_text(msg.get("content")).strip()
            raw_calls = msg.get("tool_calls") or []
            for tc in raw_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = str(fn.get("name") or tc.get("name") or "")
                if not name:
                    continue
                args = _parse_tool_args(fn.get("arguments"))
                summary = _short_args_summary(args)
                tool_calls.append({
                    "name": name,
                    "args_summary": summary,
                })
                step = f"{name}({summary})" if summary else name
                if step not in workflow_steps:
                    workflow_steps.append(step)
                for k in _PATHY_ARG_KEYS:
                    v = args.get(k)
                    if isinstance(v, str):
                        _add_file(v)
                    elif isinstance(v, list):
                        for p in v:
                            if isinstance(p, str):
                                _add_file(p)
                cmd = args.get("command") or args.get("cmd")
                if isinstance(cmd, str):
                    for p in _detect_key_paths(cmd):
                        _add_file(p)
            if text:
                first = _first_sentences(text, max_chars=220)
                if first and first not in assistant_conclusions:
                    assistant_conclusions.append(first)
        elif role == "tool":
            name = str(msg.get("name") or "")
            content = _message_text(msg.get("content"))
            lowered = content.lower()
            is_error = (
                "error" in lowered[:80]
                or lowered.startswith("traceback")
                or "failed" in lowered[:80]
            )
            excerpt = _clip(content, 240)
            tool_results.append({
                "name": name,
                "is_error": is_error,
                "excerpt": excerpt,
            })
            for p in _detect_key_paths(content):
                _add_file(p)
            if name in {"write_file", "replace"} and not is_error:
                outcome_signals.append(f"wrote via {name}")
            elif name == "run_shell_command":
                if "passed" in lowered or " ok " in lowered or "success" in lowered:
                    outcome_signals.append("shell command reported success")
                if is_error:
                    outcome_signals.append("shell command reported error")

    # Dedupe while preserving order.
    def _dedupe(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in seq:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return SkillEvidence(
        user_goals=user_goals[:6],
        assistant_conclusions=assistant_conclusions[-6:],
        tool_calls=tool_calls[-30:],
        tool_results=tool_results[-30:],
        key_files=key_files_seen[:15],
        workflow_steps=_dedupe(workflow_steps)[:20],
        outcome_signals=_dedupe(outcome_signals)[:10],
    )


def render_crystallize_context(
    evidence,
    draft_content: str | None = None,
    feedback_history: list | None = None,
) -> str:
    """Render a compact evidence context block for LLM prompts.

    Keeps each subsection small so the whole block stays well below the
    prompt budget, even for long sessions.
    """
    lines: list[str] = []
    if evidence is not None:
        if evidence.user_goals:
            lines.append("## User goals")
            for g in evidence.user_goals:
                lines.append(f"- {g}")
        if evidence.workflow_steps:
            lines.append("\n## Workflow (tool sequence)")
            for i, step in enumerate(evidence.workflow_steps, 1):
                lines.append(f"{i}. {step}")
        if evidence.tool_calls:
            lines.append("\n## Tool calls")
            for tc in evidence.tool_calls[-12:]:
                summary = tc.get("args_summary", "")
                if summary:
                    lines.append(f"- {tc.get('name', '')}({summary})")
                else:
                    lines.append(f"- {tc.get('name', '')}")
        if evidence.tool_results:
            lines.append("\n## Tool results (truncated)")
            for tr in evidence.tool_results[-8:]:
                mark = "✗" if tr.get("is_error") else "✓"
                lines.append(
                    f"- {mark} {tr.get('name', '')}: {tr.get('excerpt', '')}"
                )
        if evidence.key_files:
            lines.append("\n## Key files")
            for f in evidence.key_files:
                lines.append(f"- {f}")
        if evidence.assistant_conclusions:
            lines.append("\n## Assistant conclusions")
            for c in evidence.assistant_conclusions:
                lines.append(f"- {c}")
        if evidence.outcome_signals:
            lines.append("\n## Outcome signals")
            for s in evidence.outcome_signals:
                lines.append(f"- {s}")

    if draft_content:
        lines.append("\n## Current draft")
        lines.append(draft_content.strip())

    if feedback_history:
        lines.append("\n## Prior feedback")
        for i, f in enumerate(feedback_history, 1):
            author = getattr(f, "author", "user")
            content = getattr(f, "content", "")
            lines.append(f"{i}. [{author}] {content}")

    return "\n".join(lines).strip()
