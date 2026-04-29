"""Blueprint C — support-ticket triage with confidence-gated auto-reply.

Usage:
    uv run python -m src.triage "ticket text here"

The agent looks up a customer profile, searches a mock KB, and either
auto-sends (confidence >= 0.9) or drafts for a human reviewer.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from agentao import Agentao  # type alias for return annotation
from agentao.embedding import build_from_environment
from agentao.permissions import PermissionDecision, PermissionEngine, PermissionMode
from agentao.tools.base import Tool


# ──────────────────────────────────────────────────────────────────────────
# Mock CRM / KB — replace with real API clients in production
# ──────────────────────────────────────────────────────────────────────────

_CUSTOMERS: Dict[str, Dict[str, Any]] = {
    "alice@acme.io":  {"plan": "pro",        "ltv": 4800, "open_tickets": 1},
    "bob@startup.dev": {"plan": "free",       "ltv": 0,    "open_tickets": 3},
    "carol@bigco.com": {"plan": "enterprise", "ltv": 120000, "open_tickets": 0},
}

_KB = [
    {"q": "reset password",  "a": "Visit /settings/security → 'Forgot password'."},
    {"q": "shipping status", "a": "Orders ship within 2 business days; tracking appears in your email."},
    {"q": "refund",          "a": "Refunds are processed within 5 business days after return."},
]

_OUTBOX: list[Dict[str, Any]] = []


# ──────────────────────────────────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────────────────────────────────

class GetCustomerProfile(Tool):
    @property
    def name(self) -> str: return "get_customer_profile"
    @property
    def description(self) -> str:
        return "Look up customer plan, LTV, and number of open tickets by email."
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"email": {"type": "string"}},
            "required": ["email"],
        }
    @property
    def is_read_only(self) -> bool: return True
    def execute(self, email: str) -> str:
        profile = _CUSTOMERS.get(email.lower())
        if profile is None:
            return f"{{\"error\": \"unknown customer\", \"email\": \"{email}\"}}"
        return str(profile)


class SearchKb(Tool):
    @property
    def name(self) -> str: return "search_kb"
    @property
    def description(self) -> str:
        return "Search the knowledge base. Returns the most relevant article text or 'no match'."
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
    @property
    def is_read_only(self) -> bool: return True
    def execute(self, query: str) -> str:
        q = query.lower()
        for entry in _KB:
            if entry["q"] in q:
                return entry["a"]
        return "no match"


class DraftReply(Tool):
    def __init__(self, ticket_id: str):
        self._ticket_id = ticket_id
    @property
    def name(self) -> str: return "draft_reply"
    @property
    def description(self) -> str:
        return "Save a draft reply for human review. Use when confidence < 0.9 or policy requires escalation."
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "reason": {"type": "string", "description": "Why a human must review (one short sentence)."},
            },
            "required": ["text", "reason"],
        }
    def execute(self, text: str, reason: str) -> str:
        _OUTBOX.append({
            "kind": "draft",
            "ticket_id": self._ticket_id,
            "text": text,
            "reason": reason,
        })
        return "Draft saved for human review."


class SendReply(Tool):
    def __init__(self, ticket_id: str):
        self._ticket_id = ticket_id
    @property
    def name(self) -> str: return "send_reply"
    @property
    def description(self) -> str:
        return "Send the reply to the customer. USE ONLY when confidence > 0.9."
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["text", "confidence"],
        }
    @property
    def requires_confirmation(self) -> bool: return True
    def execute(self, text: str, confidence: float) -> str:
        _OUTBOX.append({
            "kind": "sent",
            "ticket_id": self._ticket_id,
            "text": text,
            "confidence": confidence,
        })
        return "Reply sent."


# ──────────────────────────────────────────────────────────────────────────
# PermissionEngine — gate send_reply by confidence
# ──────────────────────────────────────────────────────────────────────────

class ConfidenceGatedEngine(PermissionEngine):
    """Auto-allow send_reply only when the model claims confidence >= 0.9."""

    THRESHOLD = 0.9

    def decide(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Optional[PermissionDecision]:
        if tool_name == "send_reply":
            try:
                conf = float(tool_args.get("confidence", 0))
            except (TypeError, ValueError):
                conf = 0.0
            return (
                PermissionDecision.ALLOW
                if conf >= self.THRESHOLD
                else PermissionDecision.DENY
            )
        return super().decide(tool_name, tool_args)


# ──────────────────────────────────────────────────────────────────────────
# Agent builder
# ──────────────────────────────────────────────────────────────────────────

def build_agent(ticket_id: str) -> Agentao:
    root = Path(__file__).resolve().parent.parent
    workdir = root / "runs" / ticket_id
    workdir.mkdir(parents=True, exist_ok=True)

    # Make the skill visible inside this per-ticket workdir.
    src_skill = root / ".agentao" / "skills" / "support-triage"
    dst_skill = workdir / ".agentao" / "skills" / "support-triage"
    if not dst_skill.exists():
        dst_skill.parent.mkdir(parents=True, exist_ok=True)
        dst_skill.symlink_to(src_skill)

    engine = ConfidenceGatedEngine(project_root=workdir)
    engine.set_mode(PermissionMode.READ_ONLY)

    agent = build_from_environment(
        working_directory=workdir,
        permission_engine=engine,
    )
    agent.tools.register(GetCustomerProfile())
    agent.tools.register(SearchKb())
    agent.tools.register(DraftReply(ticket_id))
    agent.tools.register(SendReply(ticket_id))
    agent.skill_manager.activate_skill(
        "support-triage",
        task_description=f"Triage ticket {ticket_id} per the policy in this skill.",
    )
    return agent


# ──────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("body", help="Ticket body text.")
    parser.add_argument("--email", default="alice@acme.io",
                        help="Customer email (default: alice@acme.io).")
    parser.add_argument("--ticket-id", default="T-1001")
    args = parser.parse_args()

    agent = build_agent(args.ticket_id)
    try:
        reply = agent.chat(
            f"Ticket #{args.ticket_id} from {args.email}:\n\n{args.body}",
            max_iterations=20,
        )
        print(reply)
        print()
        print("OUTBOX:")
        for item in _OUTBOX:
            print(f"  - {item}")
    finally:
        agent.close()


if __name__ == "__main__":
    main()
