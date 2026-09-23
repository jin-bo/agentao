"""Blueprint C — support-ticket triage with confidence-gated auto-reply.

Usage:
    uv run python -m src.triage "ticket text here"

The agent looks up a customer profile, searches a mock KB, and either
auto-sends (confidence >= 0.9) or drafts for a human reviewer.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from agentao import Agentao  # type alias for return annotation
from agentao.embedding import build_from_environment
from agentao.permissions import (
    PermissionDecision,
    PermissionDecisionDetail,
    PermissionEngine,
    PermissionMode,
)
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
# Permission rules — this agent answers tickets; it does not touch the disk
# ──────────────────────────────────────────────────────────────────────────

# Every built-in whose ``is_read_only`` is False — the set read-only mode used
# to deny wholesale. ``save_memory`` belongs on the list: it writes
# ``.agentao/memory.db`` under this ticket's working directory.
NO_FILE_OR_SHELL = [
    {"tool": "write_file", "action": "deny"},
    {"tool": "replace", "action": "deny"},
    {"tool": "run_shell_command", "action": "deny"},
    {"tool": "save_memory", "action": "deny"},
]


# ──────────────────────────────────────────────────────────────────────────
# PermissionEngine — gate send_reply by confidence
# ──────────────────────────────────────────────────────────────────────────

class ConfidenceGatedEngine(PermissionEngine):
    """Auto-allow send_reply only when the model claims confidence >= 0.9.

    Override ``decide_detail``, not ``decide``: the runtime asks for the
    detail (the decision plus the reason it reports to the host), and
    ``decide`` is the thin wrapper over it. Overriding ``decide`` alone
    leaves the gate unreachable.
    """

    THRESHOLD = 0.9

    def _gate(self, tool_name: str, tool_args: Dict[str, Any]) -> Optional[float]:
        """The confidence this engine judges, or ``None`` for other tools."""
        if tool_name != "send_reply":
            return None
        try:
            return float(tool_args.get("confidence", 0))
        except (TypeError, ValueError):
            return 0.0

    def decide_detail(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        *,
        shell_spec: Any = None,
        decided: Any = None,
    ) -> Optional[PermissionDecisionDetail]:
        conf = self._gate(tool_name, tool_args)
        if conf is None:
            return super().decide_detail(
                tool_name, tool_args, shell_spec=shell_spec, decided=decided,
            )
        allowed = conf >= self.THRESHOLD
        return PermissionDecisionDetail(
            PermissionDecision.ALLOW if allowed else PermissionDecision.DENY,
            reason=f"host-rule:send_reply confidence={conf:.2f}",
        )

    # No ``decide`` override: the base one is already the thin wrapper
    # ``detail.decision if detail is not None else None`` over the method
    # above, so re-declaring it here would only restate it.


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

    # Not ``read-only``: that mode denies every tool whose ``is_read_only``
    # is False — draft_reply and send_reply included — before any rule is
    # consulted. Keep the agent out of the filesystem by rule instead: user
    # rules are evaluated ahead of the workspace-write preset, so these win,
    # and the engine still decides this example's own tools.
    engine = ConfidenceGatedEngine(project_root=workdir, rules=NO_FILE_OR_SHELL)
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)

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
