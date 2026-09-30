"""How a finished sub-agent run is classified, and what it is called.

:func:`_classify_subagent_outcome` decides whether the run answered;
:func:`_terminal_state` maps that onto the ``BgTaskStatus`` vocabulary both
the foreground and the background path report in. Pure functions over the
child's ``last_turn`` and history — no wrapper state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..bg_store import BgTaskStatus


# Human-readable renderings of the ``TurnOutcome.incomplete_reason`` closed
# vocabulary. The parent LLM reads these, so they say what happened in plain
# terms rather than echoing the wire token.
_INCOMPLETE_DETAILS: Dict[str, str] = {
    "no_output": "it produced no output",
    "reasoning_only": "it produced only reasoning, with no answer",
    "length_truncated": "its answer was cut off by the model's output-length limit",
    "doom_loop": "it was halted after repeating the same tool call",
    "llm_error": "the LLM API call failed",
}

# Sentinel reason for budget exhaustion. Not part of the ``incomplete_reason``
# vocabulary — ``max_iterations`` is a separate axis by design — so it gets its
# own key rather than being smuggled into that closed set.
_MAX_ITERATIONS_REASON = "max_iterations"

# The one ``_IncompleteOutcome.reason`` that names a user action rather than a
# way of stopping short. Minted in exactly one place (the ``status ==
# "cancelled"`` branch of ``_classify_subagent_outcome``) and read in exactly
# one other (``_terminal_state``), so it is a constant rather than two string
# literals free to drift apart: it is the join key between what happened and
# what every surface calls it.
_CANCELLED_REASON = "cancelled"


@dataclass(frozen=True)
class _IncompleteOutcome:
    """Why a sub-agent stopped short. ``reason`` is machine-readable."""

    reason: str
    detail: str


# Harness-authored turn text. None of these is sub-agent output, so none may
# be presented to the parent LLM as the child's "partial result" — doing so
# would attribute the harness's own notice to the sub-agent. The empty-turn
# placeholder is imported lazily (see ``_format_result``); the rest are the
# max-iterations and LLM-error notices from ``chat_loop/_runner.py`` and the
# two cancellation markers from ``runtime/turn.py``.
#
# The cancel markers are here because they are the *whole* text of a turn that
# ``AgentCancelledError`` or ``KeyboardInterrupt`` ended, so labelling them
# "Partial result" tells the parent LLM the child reported "[Cancelled:
# user-cancel]" as its work. A turn cancelled mid-stream returns whatever the
# model had produced instead, which is real output and stays labelled.
_HARNESS_NOTICE_PREFIXES = ("[LLM API error:", "[Cancelled:")
_HARNESS_NOTICE_EXACT = (
    "Maximum tool call iterations reached.",
    "[Interrupted by user]",
)


def _is_harness_notice(text: Optional[str]) -> bool:
    """True if ``text`` is agentao's own notice rather than model output.

    Used to decide what may be shown to the parent LLM as a sub-agent's
    "partial result". ``[No response]``, ``[LLM API error: …]``, "Maximum
    tool call iterations reached." and the two cancellation markers are all
    strings the harness authored on the child's behalf; labelling them as
    the child's partial work would attribute the harness's words to the
    sub-agent — the exact misreporting this whole path exists to stop.
    """
    # Deferred: ``agentao.runtime`` imports ``agentao.agents`` for
    # ``TaskComplete``, so a module-level import here is a cycle.
    from ...runtime.chat_loop._runner import EMPTY_RESPONSE_PLACEHOLDER

    body = (text or "").strip()
    if not body:
        return True
    if body == EMPTY_RESPONSE_PLACEHOLDER or body in _HARNESS_NOTICE_EXACT:
        return True
    return body.startswith(_HARNESS_NOTICE_PREFIXES)


def _find_task_complete_result(sub_agent: Any) -> Optional[str]:
    """Return the payload of the sub-agent's ``complete_task`` call, if any.

    ``CompleteTaskTool.execute`` raises ``TaskComplete``, but
    ``ToolExecutor._execute_one`` catches it and turns it into an ordinary
    tool result (``runtime/tool_executor.py:339``) — so it never propagates
    out of ``chat()`` and cannot be detected with ``except``. The durable
    signal is the tool result it leaves in the child's history.

    Returns the last such payload (``None`` if the tool was never called),
    which is both the "the agent declared itself done" flag and the answer
    it meant to hand back.
    """
    messages = getattr(sub_agent, "messages", None) or []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool" and msg.get("name") == "complete_task":
            content = msg.get("content")
            return content if isinstance(content, str) else ""
    return None


def _classify_subagent_outcome(
    *,
    outcome: Any,
    task_complete: bool,
    max_iterations_hit: bool,
    max_turns: int,
) -> Optional[_IncompleteOutcome]:
    """Decide whether a finished sub-agent run actually answered.

    Returns ``None`` when the run produced a real answer — the common
    case — and an :class:`_IncompleteOutcome` otherwise. Mirrors the
    top-level ``TurnOutcome`` contract (PR #126) one level down: a
    sub-agent that never answered must not be reported to the parent
    LLM, or to a host watching ``SubagentLifecycleEvent``, as a success.

    ``task_complete`` wins over everything: the sub-agent called
    ``complete_task``, which is an explicit "I am done" signal, and the
    turn-level classification of the call that carried it is irrelevant.
    """
    if task_complete:
        return None
    if max_iterations_hit:
        return _IncompleteOutcome(
            _MAX_ITERATIONS_REASON,
            f"it used its entire {max_turns}-turn budget without finishing",
        )
    if outcome is None:
        # No ``last_turn`` to read (a stubbed or older agent object).
        # Absence of evidence is not evidence of failure.
        return None
    if getattr(outcome, "is_answer", False):
        return None

    reason = getattr(outcome, "incomplete_reason", None)
    if reason:
        detail = _INCOMPLETE_DETAILS.get(reason, f"it stopped early ({reason})")
        return _IncompleteOutcome(reason, detail)

    status = getattr(outcome, "status", None)
    if status == "cancelled":
        return _IncompleteOutcome(_CANCELLED_REASON, "it was cancelled")
    if status == "error":
        return _IncompleteOutcome("error", "it ended with an error")
    # ``is_answer`` false with no reason and no bad status shouldn't happen;
    # report it honestly rather than papering over it as success.
    return _IncompleteOutcome("unknown", "it did not produce a complete answer")


def _terminal_state(incomplete: Optional[_IncompleteOutcome]) -> BgTaskStatus:
    """What to call a finished sub-agent run — the one mapping both paths use.

    Answers in the ``BgTaskStatus`` vocabulary that the background store, the
    foreground ``SubagentProgress`` and the public ``SubagentLifecycleEvent``
    phase all share, so a cancel cannot land in one terminal state on the
    foreground path and another on the background one.

    **Derived from the classification, never from whether an exception
    escaped.** Both paths used to decide this inline as ``"completed" if
    incomplete is None else "failed"`` and lean on an ``except
    AgentCancelledError`` to recover the cancelled case — a branch ``chat()``
    never reaches, so every running cancel was recorded as a failure (#244).
    A cancel arrives here three different ways and none of them is an
    exception by the time the wrapper sees it: ``AgentCancelledError`` and
    ``KeyboardInterrupt`` are both mapped to ``status="cancelled"`` in
    ``runtime/turn.py``, and a token cancelled mid-stream lets the turn return
    normally and is flipped to that same status in the ``finally`` there.

    ``complete_task`` and the turn budget keep the precedence
    :func:`_classify_subagent_outcome` gives them: a sub-agent that declared
    itself done and was cancelled a moment later reads ``completed``, and one
    that exhausted its budget as the cancel landed reads ``failed`` with
    ``max_iterations``. Both are the classifier's answer to what ended the
    run, and a cancel arriving after the fact does not rewrite it.
    """
    if incomplete is None:
        return "completed"
    if incomplete.reason == _CANCELLED_REASON:
        return "cancelled"
    return "failed"
