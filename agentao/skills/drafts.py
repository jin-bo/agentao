"""Project-scoped pending skill draft store for /crystallize refine workflow."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_DRAFT_DIR = Path(".agentao") / "crystallize"
_DEFAULT_DRAFT_FILENAME = "skill_draft.json"
_SAFE_SESSION_ID_RE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class SkillEvidence:
    """Structured evidence collected from the current session.

    Populated by ``collect_crystallize_evidence`` and persisted alongside the
    draft so that `/crystallize refine` and `/crystallize feedback` can reason
    about the actual tool/LLM activity, not just raw chat text.
    """

    user_goals: List[str] = field(default_factory=list)
    assistant_conclusions: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    key_files: List[str] = field(default_factory=list)
    workflow_steps: List[str] = field(default_factory=list)
    outcome_signals: List[str] = field(default_factory=list)


@dataclass
class SkillFeedbackEntry:
    """A single user feedback note attached to a skill draft."""

    author: str
    content: str
    created_at: str


@dataclass
class SkillDraft:
    session_id: str
    created_at: str
    updated_at: str
    source: str
    refined_with: Optional[str]
    suggested_name: str
    content: str
    evidence: SkillEvidence = field(default_factory=SkillEvidence)
    feedback_history: List[SkillFeedbackEntry] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _root(working_directory: Path | None) -> Path:
    return working_directory if working_directory is not None else Path.cwd()


def _draft_filename(session_id: str | None) -> str:
    if not session_id:
        return _DEFAULT_DRAFT_FILENAME
    safe = _SAFE_SESSION_ID_RE.sub("_", session_id)[:64].strip("_")
    return f"skill_draft_{safe}.json" if safe else _DEFAULT_DRAFT_FILENAME


def get_skill_draft_path(
    working_directory: Path | None = None,
    session_id: str | None = None,
) -> Path:
    return _root(working_directory) / _DRAFT_DIR / _draft_filename(session_id)


def save_skill_draft(
    draft: SkillDraft,
    working_directory: Path | None = None,
    session_id: str | None = None,
) -> Path:
    sid = session_id if session_id is not None else draft.session_id
    path = get_skill_draft_path(working_directory, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    draft.updated_at = _now_iso()
    path.write_text(json.dumps(asdict(draft), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _evidence_from_dict(data: Any) -> SkillEvidence:
    if not isinstance(data, dict):
        return SkillEvidence()

    def _str_list(val: Any) -> List[str]:
        if not isinstance(val, list):
            return []
        return [str(x) for x in val if isinstance(x, (str, int, float))]

    def _dict_list(val: Any) -> List[Dict[str, Any]]:
        if not isinstance(val, list):
            return []
        return [dict(x) for x in val if isinstance(x, dict)]

    return SkillEvidence(
        user_goals=_str_list(data.get("user_goals")),
        assistant_conclusions=_str_list(data.get("assistant_conclusions")),
        tool_calls=_dict_list(data.get("tool_calls")),
        tool_results=_dict_list(data.get("tool_results")),
        key_files=_str_list(data.get("key_files")),
        workflow_steps=_str_list(data.get("workflow_steps")),
        outcome_signals=_str_list(data.get("outcome_signals")),
    )


def _feedback_from_list(data: Any) -> List[SkillFeedbackEntry]:
    if not isinstance(data, list):
        return []
    out: List[SkillFeedbackEntry] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append(SkillFeedbackEntry(
            author=str(item.get("author", "user")),
            content=str(item.get("content", "")),
            created_at=str(item.get("created_at", "")),
        ))
    return out


def load_skill_draft(
    working_directory: Path | None = None,
    session_id: str | None = None,
) -> Optional[SkillDraft]:
    path = get_skill_draft_path(working_directory, session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return SkillDraft(
            session_id=data.get("session_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            source=data.get("source", "suggest"),
            refined_with=data.get("refined_with"),
            suggested_name=data.get("suggested_name", ""),
            content=data.get("content", ""),
            evidence=_evidence_from_dict(data.get("evidence")),
            feedback_history=_feedback_from_list(data.get("feedback_history")),
            open_questions=[
                str(q) for q in (data.get("open_questions") or [])
                if isinstance(q, (str, int, float))
            ],
        )
    except TypeError:
        return None


def clear_skill_draft(
    working_directory: Path | None = None,
    session_id: str | None = None,
) -> bool:
    path = get_skill_draft_path(working_directory, session_id)
    if path.exists():
        path.unlink()
        return True
    return False


def new_draft(
    content: str,
    suggested_name: str,
    session_id: str = "",
    source: str = "suggest",
    evidence: Optional[SkillEvidence] = None,
) -> SkillDraft:
    now = _now_iso()
    return SkillDraft(
        session_id=session_id,
        created_at=now,
        updated_at=now,
        source=source,
        refined_with=None,
        suggested_name=suggested_name,
        content=content,
        evidence=evidence if evidence is not None else SkillEvidence(),
    )


def append_skill_feedback(
    draft: SkillDraft,
    text: str,
    author: str = "user",
) -> SkillDraft:
    """Append a feedback entry to ``draft.feedback_history`` in place.

    Returns the same draft for chaining. Whitespace-only text is rejected.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("Feedback text must not be empty")
    draft.feedback_history.append(SkillFeedbackEntry(
        author=author,
        content=cleaned,
        created_at=_now_iso(),
    ))
    return draft


def summarize_draft_status(draft: SkillDraft) -> Dict[str, Any]:
    """Return a compact dict view of a draft's headline metadata."""
    ev = draft.evidence or SkillEvidence()
    return {
        "name": draft.suggested_name or "",
        "source": draft.source,
        "refined_with": draft.refined_with,
        "updated_at": draft.updated_at,
        "feedback_count": len(draft.feedback_history or []),
        "tool_call_count": len(ev.tool_calls),
        "tool_result_count": len(ev.tool_results),
        "workflow_step_count": len(ev.workflow_steps),
        "key_file_count": len(ev.key_files),
    }


_FRONTMATTER_RE = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_NAME_LINE_RE = re.compile(r"^(\s*name\s*:\s*)(.*?)\s*$", re.MULTILINE)


def extract_skill_name(skill_md: str) -> str | None:
    """Extract the `name:` value from the YAML frontmatter of a SKILL.md."""
    m = _FRONTMATTER_RE.match(skill_md or "")
    if not m:
        return None
    block = m.group(1)
    nm = _NAME_LINE_RE.search(block)
    if not nm:
        return None
    value = nm.group(2).strip().strip('"').strip("'")
    return value or None


def replace_skill_name(skill_md: str, new_name: str) -> str:
    """Replace `name:` in the frontmatter; raise if no frontmatter present."""
    m = _FRONTMATTER_RE.match(skill_md or "")
    if not m:
        raise ValueError("SKILL.md is missing YAML frontmatter")
    block = m.group(1)
    if _NAME_LINE_RE.search(block):
        new_block = _NAME_LINE_RE.sub(lambda _m: f"{_m.group(1)}{new_name}", block, count=1)
    else:
        new_block = f"name: {new_name}\n{block}"
    prefix_end = m.end()
    # Reconstruct: replace the captured block within the original match
    old_fm = m.group(0)
    # Rebuild frontmatter literal preserving leading/trailing fences
    new_fm = f"---\n{new_block}\n---\n"
    # Drop any extra trailing newline beyond what old_fm had, keep body intact
    return new_fm + skill_md[prefix_end:]
