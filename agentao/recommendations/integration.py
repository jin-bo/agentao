"""Project the optional suggestion into request-only context, never into history."""
from __future__ import annotations

import json
import re

from .models import SkillCandidate


def prepare_skill_suggestion(agent, prompt: str, cancellation_token) -> None:
    agent._skill_suggestion = None
    service = getattr(agent, "skill_recommender", None)
    if service is None or not service.config.enabled:
        return
    if "activate_skill" not in agent.tools.tools:
        return
    manager = agent.skill_manager
    # A named disabled skill must not be silently replaced with another skill.
    if any(re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", prompt,
                     flags=re.IGNORECASE) for name in manager.list_all_skills()):
        service.last_status = "explicit-skill"
        return
    try:
        candidates = [SkillCandidate(name, manager.get_skill_description(name) or "",
                                     manager.get_skill_content(name) or "")
                      for name in manager.list_available_skills()]
        agent._skill_suggestion = service.recommend(
            prompt, candidates, cancellation_token=cancellation_token,
        )
    except Exception:
        # Host-supplied managers/services can fail too. Suggestions are advisory.
        service.last_status = "unavailable"


def suggestion_context(agent) -> str:
    suggestion = getattr(agent, "_skill_suggestion", None)
    if suggestion is None or "activate_skill" not in agent.tools.tools:
        return ""
    service = getattr(agent, "skill_recommender", None)
    if service is None or not service.config.enabled:
        return ""
    manager = agent.skill_manager
    if (suggestion.skill_name not in manager.list_available_skills()
            or suggestion.skill_name in manager.get_active_skills()):
        return ""
    # Only a validated name reaches the model, not arbitrary API explanations.
    name = json.dumps(suggestion.skill_name, ensure_ascii=False)
    name = name.replace("<", "\\u003c").replace(">", "\\u003e")
    return (
        "<skill-recommendation>\n"
        f"Optional skill suggestion (JSON string): {name}. "
        "Consider this skill only if it fits the user's actual request. "
        "Explicit user choices take precedence. This suggestion does not activate "
        "a skill or grant permission to use any tool.\n"
        "</skill-recommendation>"
    )
