"""Crystallization: promote session insights to long-term memory (MemoryCrystallizer)
and generate SKILL.md from session patterns (SkillCrystallizer).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Tuple

from .models import (
    CrystallizationProposal,
    MemoryRecord,
    MemoryReviewItem,
    SaveMemoryRequest,
)

if TYPE_CHECKING:
    from .manager import MemoryManager


# ---------------------------------------------------------------------------
# Rule patterns (regex, type, scope_hint)
#
# Each pattern captures a phrase that signals an explicit user expression.
# Single occurrence is enough — repetition across summaries only raises
# confidence, it isn't a separate gate.
# ---------------------------------------------------------------------------

_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    # ----- preference (user scope) -----
    (re.compile(r"\bi (?:prefer|like|use|want)\s+([^.,;\n]{3,80})", re.I), "preference", "user"),
    (re.compile(r"我(?:喜欢|偏好|倾向(?:于)?|想用)\s*([^。，；\n]{2,40})"), "preference", "user"),
    # ----- constraint (project scope) -----
    (re.compile(r"\b(?:always|never|don'?t|do not|must not|must)\s+([^.,;\n]{3,80})", re.I), "constraint", "project"),
    (re.compile(r"(?:不要|永远不|从不|必须|禁止)\s*([^。，；\n]{2,40})"), "constraint", "project"),
    # ----- decision (project scope) -----
    (re.compile(r"\b(?:we decided to|let'?s use|switching to|going to use|chose)\s+([^.,;\n]{3,80})", re.I), "decision", "project"),
    (re.compile(r"(?:我们)?(?:决定|选择|改用|采用)\s*([^。，；\n]{2,40})"), "decision", "project"),
    # ----- workflow (project scope) -----
    (re.compile(r"\b(?:workflow|process|pipeline)(?: is)?:\s*([^.\n]{5,120})", re.I), "workflow", "project"),
    (re.compile(r"(?:工作流(?:程)?|流程)(?:是|为)?[:：]\s*([^。\n]{2,80})"), "workflow", "project"),
]


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _proposal_key(captured: str, type_: str) -> str:
    """Build a stable normalized key from the captured phrase + type prefix."""
    tokens = re.findall(r"[A-Za-z\u4e00-\u9fff]+", captured.lower())[:4]
    if not tokens:
        return f"{type_}_unknown"
    return f"{type_}_" + "_".join(tokens)


def _make_title(captured: str, type_: str) -> str:
    """Human-readable title from captured phrase."""
    cleaned = re.sub(r"\s+", " ", captured.strip())
    if len(cleaned) > 60:
        cleaned = cleaned[:57] + "…"
    return f"{type_.title()}: {cleaned}"


class MemoryCrystallizer:
    """Conservative rule-based extractor: pulls preference/constraint/decision/workflow
    candidates from session summaries.

    Extraction is purely lexical (no LLM call). Each match becomes a
    `CrystallizationProposal` and is submitted to the review queue rather
    than written directly into live memory.
    """

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract_from_summary(
        self, summary: str, session_id: str = ""
    ) -> List[CrystallizationProposal]:
        """Run all rule patterns against a single text blob. Returns one
        proposal per unique ``(scope, key_normalized)`` match.

        This is the per-string primitive. **The caller is expected to feed
        raw user text** (e.g. one user message), not LLM-narrated summary
        prose. Use :meth:`extract_from_user_messages` to apply this safely
        to a list of conversation messages.

        The historical name is preserved for callsite stability; the
        "_summary" suffix should be read as "text segment", not as
        "session summary".
        """
        if not summary or not summary.strip():
            return []

        seen_keys: set = set()
        proposals: List[CrystallizationProposal] = []

        for pattern, type_, scope in _PATTERNS:
            for m in pattern.finditer(summary):
                captured = m.group(1).strip()
                if not captured:
                    continue
                key = _proposal_key(captured, type_)
                dedupe_key = (scope, key)
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)

                # Evidence: surrounding context up to 240 chars
                start = max(0, m.start() - 40)
                end = min(len(summary), m.end() + 40)
                evidence = summary[start:end].strip()
                if len(evidence) > 240:
                    evidence = evidence[:237] + "…"

                proposals.append(CrystallizationProposal(
                    scope=scope,
                    type=type_,
                    key_normalized=key,
                    title=_make_title(captured, type_),
                    content=captured,
                    tags=[type_],
                    evidence=evidence,
                    source_session=session_id,
                    occurrences=1,
                    confidence="auto_summary",
                ))
        return proposals

    def extract_from_user_messages(
        self,
        messages: List[dict],
        session_id: str = "",
    ) -> List[CrystallizationProposal]:
        """Extract proposals from the **raw user messages** in *messages*.

        This is the safe entry point: only ``role == "user"`` messages are
        scanned, so assistant narration, tool output, and system prompts can
        never trigger a false match — even if they happen to contain the
        words ``"I prefer"`` or ``"never"``.

        Content handling:

        - Plain string content (the current chat path): used directly.
        - List-of-blocks content (defensive — multimodal/tool-use shapes):
          joined text from ``{"type": "text", "text": ...}`` blocks.
        - Leading ``[PIN]`` marker is stripped before pattern matching, so
          pinned messages still crystallize correctly.
        - Empty / non-text messages are skipped.

        Within-call dedup: same ``(scope, key_normalized)`` matched in
        multiple user messages folds into one proposal whose ``occurrences``
        equals the number of matching messages. ``confidence`` is raised to
        ``inferred`` once ``occurrences >= 2``.
        """
        merged: Dict[Tuple[str, str], CrystallizationProposal] = {}
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            text = self._user_message_text(msg.get("content"))
            if not text:
                continue
            if text.startswith("[PIN]"):
                text = text[len("[PIN]"):].lstrip()
            for p in self.extract_from_summary(text, session_id):
                key = (p.scope, p.key_normalized)
                if key in merged:
                    existing = merged[key]
                    existing.occurrences += 1
                    existing.evidence = p.evidence
                    existing.source_session = p.source_session
                else:
                    merged[key] = p

        for p in merged.values():
            if p.occurrences >= 2:
                p.confidence = "inferred"
        return list(merged.values())

    @staticmethod
    def _user_message_text(content) -> str:
        """Normalize a message ``content`` field to a single string.

        Returns ``""`` for empty/None/unsupported shapes so callers can
        skip the message safely.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return " ".join(p for p in parts if p)
        return ""

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def submit_to_review(
        self,
        proposals: List[CrystallizationProposal],
        manager: "MemoryManager",
    ) -> List[MemoryReviewItem]:
        """Persist proposals into the review queue (NOT into live memories)."""
        saved: List[MemoryReviewItem] = []
        store = manager.project_store
        now = _now_iso()
        for p in proposals:
            item = MemoryReviewItem(
                id=uuid.uuid4().hex[:12],
                scope=p.scope,
                type=p.type,
                key_normalized=p.key_normalized,
                title=p.title,
                content=p.content,
                tags=p.tags,
                evidence=p.evidence,
                source_session=p.source_session,
                occurrences=p.occurrences,
                confidence=p.confidence,
                status="pending",
                created_at=now,
                updated_at=now,
            )
            saved.append(store.upsert_review_item(item))
        return saved

    def promote(
        self,
        item: MemoryReviewItem,
        manager: "MemoryManager",
    ) -> MemoryRecord:
        """Approve a queue item and write it into live memories with
        ``source='crystallized'``. Marks the queue row as ``approved``.
        """
        request = SaveMemoryRequest(
            key=item.key_normalized,
            value=item.content,
            tags=list(item.tags),
            scope=item.scope,
            type=item.type,
            source="crystallized",
        )
        record = manager.upsert(request)
        manager.project_store.update_review_status(item.id, "approved")
        return record


# Mirror SkillManager path conventions
_GLOBAL_SKILLS_DIR = Path.home() / ".agentao" / "skills"

SUGGEST_SYSTEM_PROMPT = """\
You are a skill extraction assistant for Agentao, an AI coding agent.
Analyze the provided session transcript and suggest ONE reusable skill that captures a useful, repeatable workflow.

Output ONLY a valid SKILL.md in this exact format — nothing before or after:

---
name: <snake_case_identifier>
description: <1-2 sentences: when to activate this skill and what it helps with>
---

# <Human-Readable Skill Title>

## When to use
- <trigger condition 1>
- <trigger condition 2>
- <trigger condition 3>

## Steps
1. <step 1>
2. <step 2>
3. <step 3>

Keep it concise and actionable. Use concrete, imperative language.
If no clear repeatable pattern exists, output exactly: NO_PATTERN_FOUND"""


def suggest_prompt(session_content: str) -> str:
    """Build the user message for LLM skill suggestion."""
    truncated = session_content[-3000:] if len(session_content) > 3000 else session_content
    return (
        "Analyze this session transcript and suggest a reusable skill that captures "
        "the most useful repeated pattern:\n\n"
        f"{truncated}"
    )


def _extract_text(llm_response) -> str:
    """Extract text content from an LLM response object."""
    try:
        return llm_response.choices[0].message.content or ""
    except Exception:
        return str(llm_response)


class SkillCrystallizer:
    """Writes skill drafts to the skills/ directory."""

    def create(self, name: str, scope: str, skill_md_content: str) -> Path:
        """Write SKILL.md to the appropriate skills/ directory.

        Args:
            name: Directory name for the skill (e.g. "python-testing")
            scope: "global" (~/.agentao/skills/) or "project" (cwd/.agentao/skills/)
            skill_md_content: Full SKILL.md file content

        Returns:
            Path to the written SKILL.md file.
        """
        if scope == "global":
            skills_dir = _GLOBAL_SKILLS_DIR
        else:
            skills_dir = Path.cwd() / ".agentao" / "skills"
        target = skills_dir / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill_md_content, encoding="utf-8")
        return target
