"""Crystallization: promote session insights to long-term memory (MemoryCrystallizer)
and generate SKILL.md from session patterns (SkillCrystallizer).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

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

# NO-OP gates — evaluate IN ORDER, stop at the first that fires

Each gate is an advisory filter. They are listed cheap-deterministic-first; do not skip ahead.

1. covered_by_existing_skill
   If the workflow is already substantially covered by one of the
   "Available skills" listed in the user message, fire this gate.
   Name the existing skill in the reason.
2. not_concrete_steps
   If you cannot express the workflow as an ordered, imperative step
   list (3+ concrete steps), fire this gate.
3. ordinary_agent_knowledge
   If the workflow is ordinary coding knowledge a competent agent should
   already know without this session's project-specific evidence (e.g.
   "use git status to see changes"), fire this gate.
4. too_session_specific
   If the workflow is only useful for this one artifact / file /
   incident, with little chance of future reuse on similar tasks, fire
   this gate.

When ANY gate fires, output exactly one of these forms — nothing else:

NO_PATTERN_FOUND:<gate_id> <one-line reason>

where <gate_id> is one of (exact spelling, lowercase, no other values):
covered_by_existing_skill | not_concrete_steps | ordinary_agent_knowledge | too_session_specific

Bare "NO_PATTERN_FOUND" (no gate id) is also accepted as a fallback but is
discouraged — prefer the gated form so the user sees why you stopped.

# When NO gate fires

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

Keep it concise and actionable. Use concrete, imperative language."""

# Canonical NO-OP gate identifiers. Kept in the same order as the gates
# in SUGGEST_SYSTEM_PROMPT. Used by the CLI parser as a whitelist —
# unknown ids fall back to the legacy bare-`NO_PATTERN_FOUND` branch.
NOOP_GATE_IDS: Tuple[str, ...] = (
    "covered_by_existing_skill",
    "not_concrete_steps",
    "ordinary_agent_knowledge",
    "too_session_specific",
)


def parse_noop_skip(text: str) -> Optional[Tuple[Optional[str], str]]:
    """Parse a model response for the NO-OP skip contract.

    Returns:
        ``None`` if the text is not a NO-OP signal (i.e. it is a real draft).
        ``(None, "")`` for the legacy bare ``NO_PATTERN_FOUND`` form.
        ``(gate_id, reason)`` for the gated form when ``gate_id`` is in the
        :data:`NOOP_GATE_IDS` whitelist. Unknown gate ids fall back to the
        legacy form ``(None, "")`` so older models that emit unrecognized
        ids do not crash the CLI.
    """
    if not text:
        return None
    head = text.strip().split("\n", 1)[0].strip()
    if head == "NO_PATTERN_FOUND":
        return (None, "")
    if not head.startswith("NO_PATTERN_FOUND:"):
        return None
    payload = head[len("NO_PATTERN_FOUND:"):].strip()
    if not payload:
        return (None, "")
    gate_id, _, reason = payload.partition(" ")
    gate_id = gate_id.strip()
    reason = reason.strip()
    if gate_id not in NOOP_GATE_IDS:
        return (None, "")
    return (gate_id, reason)


def suggest_prompt(
    session_content: str,
    evidence_text: str = "",
    available_skills_text: str = "",
) -> str:
    """Build the user message for LLM skill suggestion.

    ``evidence_text`` is a pre-rendered structured-evidence block (tool
    calls, workflow, key files, etc.). When present, it is shown first so
    the model grounds the draft in actual tool activity rather than in
    narrated chat text.

    ``available_skills_text`` is a pre-rendered ``name — description``
    block (one skill per line) covering the skills already installed in
    this workspace. Shown first so the model can apply the
    ``covered_by_existing_skill`` NO-OP gate without hallucinating
    skills it has never seen. Caller is responsible for sort order and
    truncation; this function just lays the block down verbatim.
    """
    truncated = session_content[-3000:] if len(session_content) > 3000 else session_content
    evidence_block = evidence_text[-4000:] if evidence_text else ""
    parts = []
    if available_skills_text:
        parts.append(
            "Apply the `covered_by_existing_skill` gate against this list "
            "before drafting anything new.\n"
        )
        parts.append("# Available skills (do not duplicate)\n")
        parts.append(available_skills_text)
        parts.append("")
    parts.append(
        "Suggest a reusable skill based on this session. "
        "Ground the draft in the structured evidence (tool calls, files, outcomes); "
        "the raw transcript is secondary context only.\n",
    )
    if evidence_block:
        parts.append("# Structured evidence\n")
        parts.append(evidence_block)
        parts.append("")
    parts.append("# Recent transcript excerpt\n")
    parts.append(truncated)
    return "\n".join(parts)


FEEDBACK_SYSTEM_PROMPT = """\
You are rewriting an Agentao SKILL.md draft to incorporate user feedback.

Rules:
- The user's latest feedback takes priority; earlier feedback is context.
- Stay grounded in the structured evidence. Do NOT invent tools, files, or
  outcomes that do not appear in the evidence.
- Keep the YAML frontmatter (---/name/description/---) valid.
- Preserve the draft's useful structure (When to use / Steps) unless the
  feedback explicitly asks to restructure it.
- Output ONLY the complete rewritten SKILL.md. No preamble, no commentary,
  no code fences.
"""


def feedback_prompt(
    draft_content: str,
    evidence_text: str,
    latest_feedback: str,
    feedback_history_text: str = "",
) -> str:
    """Build the user message for feedback-driven draft rewrite."""
    evidence_block = evidence_text[-4000:] if evidence_text else ""
    history_block = feedback_history_text.strip()
    parts = [
        "# Current draft",
        draft_content.strip(),
        "",
        "# Structured evidence",
        evidence_block or "(none)",
        "",
    ]
    if history_block:
        parts.extend(["# Prior feedback", history_block, ""])
    parts.extend([
        "# Latest user feedback (apply this)",
        latest_feedback.strip(),
        "",
        "Return the rewritten complete SKILL.md now.",
    ])
    return "\n".join(parts)


REFINE_SYSTEM_PROMPT = """\
You are refining an existing Agentao SKILL.md draft using skill-authoring best practices.

Rules:
- Preserve the draft's original intent and scope. Do NOT introduce new capabilities that the transcript does not support.
- If the draft is already solid, make only minimal improvements.
- Improve where appropriate:
  - description: make it triggering (concrete when-to-use cues), covering BOTH what it does AND when to activate it.
  - "When to use": concrete user phrases / contexts.
  - "Steps": clear, imperative, minimal, ordered.
  - Writing style: concise and actionable.
- Keep the YAML frontmatter (---/name/description/---) valid.
- Output ONLY the complete SKILL.md content. No preamble, no commentary, no code fences.
"""


def refine_prompt(
    draft_content: str,
    session_content: str,
    skill_creator_guidance: str,
    evidence_text: str = "",
) -> str:
    """Build the user message for LLM skill-draft refinement.

    Four blocks are optionally provided: current draft, structured evidence,
    recent transcript excerpt, and a selected skill-creator guidance excerpt.
    """
    transcript = session_content[-3000:] if len(session_content) > 3000 else session_content
    guidance = skill_creator_guidance[:2500] if skill_creator_guidance else ""
    evidence_block = evidence_text[-4000:] if evidence_text else ""
    parts = [
        "# Current draft",
        draft_content,
        "",
    ]
    if evidence_block:
        parts.extend(["# Structured evidence", evidence_block, ""])
    parts.extend([
        "# Recent session transcript excerpt",
        transcript,
        "",
        "# Skill-creator guidance excerpt",
        guidance,
        "",
        "Return the improved complete SKILL.md now.",
    ])
    return "\n".join(parts)


def load_skill_creator_guidance() -> str:
    """Load a curated slice of skills/skill-creator/SKILL.md.

    Returns an empty string when the bundled skill is not available.
    """
    try:
        from agentao.skills.manager import _BUNDLED_SKILLS_DIR
    except Exception:
        return ""
    candidate = _BUNDLED_SKILLS_DIR / "skill-creator" / "SKILL.md"
    if not candidate.exists():
        # Also try ~/.agentao/skills as a fallback (installed location).
        alt = Path.home() / ".agentao" / "skills" / "skill-creator" / "SKILL.md"
        if not alt.exists():
            return ""
        candidate = alt
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return ""
    # Extract the "Write the SKILL.md" + "Skill Writing Guide" sections if present,
    # otherwise fall back to a head slice. This keeps the prompt tight.
    marker = "### Write the SKILL.md"
    idx = text.find(marker)
    if idx != -1:
        excerpt = text[idx : idx + 2500]
    else:
        excerpt = text[:2500]
    return excerpt


def _extract_text(llm_response) -> str:
    """Extract text content from an LLM response object."""
    try:
        return llm_response.choices[0].message.content or ""
    except Exception:
        return str(llm_response)


class SkillCrystallizer:
    """Writes skill drafts to the skills/ directory."""

    def create(
        self,
        name: str,
        scope: str,
        skill_md_content: str,
        *,
        project_root: Optional[Path] = None,
    ) -> Path:
        """Write SKILL.md to the appropriate skills/ directory.

        Args:
            name: Directory name for the skill (e.g. "python-testing").
            scope: ``global`` (``~/.agentao/skills/``) or ``project``
                (``<project_root>/.agentao/skills/``).
            skill_md_content: Full SKILL.md file content.
            project_root: Root used when ``scope == "project"``. Callers
                should pass the agent's working directory so ACP or
                background sessions save skills under the session's repo
                rather than the process cwd. Defaults to :func:`Path.cwd`
                for backwards compatibility.

        Returns:
            Path to the written SKILL.md file.
        """
        if scope == "global":
            skills_dir = _GLOBAL_SKILLS_DIR
        else:
            base = Path(project_root) if project_root is not None else Path.cwd()
            skills_dir = base / ".agentao" / "skills"
        target = skills_dir / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill_md_content, encoding="utf-8")
        return target
