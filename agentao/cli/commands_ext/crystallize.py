"""`/crystallize` slash command — skill draft suggestion, feedback, refine, create."""

from __future__ import annotations

import json as _json
import re as _re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

import readchar
from rich.panel import Panel

from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


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
    return _re.sub(r'[^a-z0-9-]', '-', (raw or "").lower()).strip('-')


# ---------------------------------------------------------------------------
# Evidence collection for /crystallize
# ---------------------------------------------------------------------------

# Argument keys that most often carry a concrete file path.
_PATHY_ARG_KEYS = (
    "file_path", "path", "filename", "file", "target", "source", "dest",
    "input_file", "output_file",
)

_SENTENCE_SPLIT_RE = _re.compile(r"(?<=[.。!?！？\n])\s+")
_FILE_HINT_RE = _re.compile(
    r"(?:(?<=[\s`(])|^)((?:/|\./|[A-Za-z0-9_\-.]+/)[A-Za-z0-9_\-./]+\.[A-Za-z0-9]+)"
)


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _first_sentences(text: str, *, max_chars: int = 200) -> str:
    """Return the first sentence(s) of text, capped at ``max_chars``."""
    text = (text or "").strip()
    if not text:
        return ""
    first = _SENTENCE_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    return _clip(first, max_chars)


def _message_text(content: Any) -> str:
    """Normalize a message content field to a single string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text" and b.get("text"):
                    parts.append(str(b["text"]))
                elif "content" in b and isinstance(b["content"], str):
                    parts.append(b["content"])
        return "\n".join(p for p in parts if p)
    return ""


def _parse_tool_args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = _json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if isinstance(data, dict):
            return data
    return {}


def _short_args_summary(args: Dict[str, Any]) -> str:
    """Build a one-line summary from tool args, clipping long values."""
    if not args:
        return ""
    parts: list[str] = []
    for key, val in list(args.items())[:4]:
        val_str = str(val).replace("\n", " ")
        val_str = _clip(val_str, 80)
        parts.append(f"{key}={val_str}")
    return ", ".join(parts)


def _detect_key_paths(blob: str) -> List[str]:
    if not blob:
        return []
    return list(dict.fromkeys(_FILE_HINT_RE.findall(blob)))


def collect_crystallize_evidence(cli: AgentaoCLI):
    """Walk the current conversation history and extract structured evidence.

    Returns a :class:`SkillEvidence` populated from user messages, assistant
    conclusions (non tool-call assistant content), ``assistant.tool_calls``,
    and ``role="tool"`` result messages. Long tool outputs are truncated so
    this can safely be embedded in an LLM prompt.
    """
    from ...skills.drafts import SkillEvidence

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


def handle_crystallize_command(cli: AgentaoCLI, args: str = "") -> None:
    """Handle /crystallize [suggest|feedback|revise|refine|create [name]|status|clear] commands."""
    from ...memory.crystallizer import (
        SkillCrystallizer,
        SUGGEST_SYSTEM_PROMPT,
        suggest_prompt,
        REFINE_SYSTEM_PROMPT,
        refine_prompt,
        FEEDBACK_SYSTEM_PROMPT,
        feedback_prompt,
        load_skill_creator_guidance,
        _extract_text,
    )
    from ...skills.drafts import (
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
        console.print("\n[dim]Analyzing session to generate skill draft...[/dim]")
        try:
            response = cli.agent.llm.chat(
                messages=[
                    {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
                    {"role": "user", "content": suggest_prompt(session_content, evidence_text)},
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
        console.print("\n[dim]Analyzing session to generate skill draft...[/dim]")
        try:
            response = cli.agent.llm.chat(
                messages=[
                    {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
                    {"role": "user", "content": suggest_prompt(session_content, evidence_text)},
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
