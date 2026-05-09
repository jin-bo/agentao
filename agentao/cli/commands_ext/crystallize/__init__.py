"""``/crystallize`` slash command — skill draft suggestion, feedback, refine, create.

Public surface (preserved from the pre-split single-file module):

- :func:`handle_crystallize_command` — the ``/crystallize`` dispatcher
- :func:`collect_crystallize_evidence` — walk messages → SkillEvidence
- :func:`render_crystallize_context` — SkillEvidence → prompt block
- :func:`render_available_skills_summary` — duplication-gate context

``_AVAILABLE_SKILLS_BUDGET`` is also re-exported because
``tests/test_skill_crystallize_enhancement.py`` imports it directly.

Layering (each row only depends on rows above):
    _helpers   ← text-shaping helpers + regex constants + session collector
    _evidence  ← collect_crystallize_evidence + render_crystallize_context
    _suggest   ← _AVAILABLE_SKILLS_BUDGET + render_available_skills_summary
                 + _suggest_draft_or_skip + _render_noop_skip
    _feedback  ← _apply_feedback_to_draft + _prompt_scope
    _handler   ← handle_crystallize_command (top-level dispatcher)
"""

from __future__ import annotations

from ._evidence import collect_crystallize_evidence, render_crystallize_context
from ._handler import handle_crystallize_command
from ._suggest import _AVAILABLE_SKILLS_BUDGET, render_available_skills_summary

__all__ = [
    "_AVAILABLE_SKILLS_BUDGET",
    "collect_crystallize_evidence",
    "handle_crystallize_command",
    "render_available_skills_summary",
    "render_crystallize_context",
]
