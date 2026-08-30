"""``ParsedHookOutput`` / ``ResolvedHookOutput`` — the conformance plan §4.1.

**Two types, not one.** What a hook's JSON *says* and what the runtime *does* are
different objects, and the second is a function of the first **plus the exit
code**. Collapsing them is why today's parser writes straight into runtime fields
and returns early after the first recognized key: a hook emitting more than one
gets only the first honored, and the reference's precedence — ``continue`` over
any event-specific decision — is unimplementable as more ``if`` branches, because
precedence needs every field parsed *before* anything is decided.

**The parse layer is deliberately wider than the disposition layer.** It can
represent a ``defer`` that ``_profile`` degrades to ``deny``, and a
``suppressOriginalPrompt`` the profile ignores. A value that cannot be *parsed*
cannot be degraded with a reason, only silently dropped — and silently dropping a
legal field is what §1's third rule forbids.

Named ``ParsedHookOutput`` rather than ``HookOutcome``: that name is taken
(``runtime/chat_loop/_outcomes.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

PermissionVerdict = Literal["allow", "deny", "ask", "defer"]


# --------------------------------------------------------------------------
# What one hook's stdout claims
# --------------------------------------------------------------------------

@dataclass
class UniversalFields:
    """The five fields every event accepts — subject to per-event delivery.

    ``continue_processing`` outranks the event decision, is itself outranked by
    exit 2, and is honored only where ``_profile.honors_continue`` says so.
    """

    continue_processing: bool | None = None
    stop_reason: str | None = None
    system_message: str | None = None
    terminal_sequence: str | None = None
    suppress_output: bool = False


@dataclass
class PreToolUseDecision:
    permission: PermissionVerdict | None = None
    reason: str | None = None
    updated_tool_input: dict[str, Any] | None = None


@dataclass
class PostToolUseDecision:
    block: bool = False
    reason: str | None = None
    updated_tool_output: Any = None


@dataclass
class UserPromptSubmitDecision:
    block: bool = False
    reason: str | None = None
    #: Parsed, and **not acted on** in profile-1: agentao's block message never
    #: contains the prompt, so there is nothing to suppress. Representing what
    #: the table then declines is the same discipline ``defer`` gets.
    suppress_original_prompt: bool = False


@dataclass
class BlockDecision:
    """``Stop``, ``PreCompact`` — and ``PostToolUseFailure``.

    The third member was contested for seven revisions and is now measured:
    the event honors a top-level ``decision``, and its effect is *feedback and
    continue*, not a stop.
    """

    block: bool = False
    reason: str | None = None


@dataclass
class SessionStartDecision:
    #: Parsed, ignored in profile-1 — the sink is not equivalent (§5.1).
    reload_skills: bool = False


HookDecision = Union[
    PreToolUseDecision,
    PostToolUseDecision,
    UserPromptSubmitDecision,
    BlockDecision,
    SessionStartDecision,
]


@dataclass
class ParsedHookOutput:
    """One hook's stdout, parsed — nothing resolved."""

    universal: UniversalFields = field(default_factory=UniversalFields)
    #: ``hookSpecificOutput.additionalContext``. Above the union rather than
    #: inside it because six of the eight events carry it; *where* it goes is
    #: routing (§5.2), not parsing.
    additional_context: list[str] = field(default_factory=list)
    #: Keys the profile does not implement, kept as names only, for the one-shot
    #: diagnostic. Their presence is never an error.
    unknown_fields: list[str] = field(default_factory=list)
    #: stdout when the state is ``plain`` (§4.2).
    plain_text: str | None = None
    decision: HookDecision | None = None

    @property
    def blocking_reason(self) -> str | None:
        """The reason a block carries, whatever shape the decision took.

        ``resolve()`` needs one accessor here: on exit 2 the reference uses the
        JSON's blocking reason when it has one and stderr otherwise.
        """
        decision = self.decision
        if isinstance(
            decision,
            (PreToolUseDecision, PostToolUseDecision, UserPromptSubmitDecision, BlockDecision),
        ):
            return decision.reason
        # ``SessionStartDecision`` is the one member with no reason to carry.
        return None


# --------------------------------------------------------------------------
# What the runtime does
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Allow:
    """No rule asked for anything."""


@dataclass(frozen=True)
class Block:
    """Blocks one action — a tool call, a prompt, a compaction.

    Distinct from :class:`Stop`, and the distinction is the point: on
    ``PreToolUse`` a block prevents one call and lets the model try something
    else, while a stop ends the turn. Folding one into the other because a
    verdict field happens to be there is the semantic divergence §1 exists to
    prevent.
    """

    reason: str | None = None


@dataclass(frozen=True)
class Stop:
    """Ends processing. Rank 1 of §5.4's lattice — by *effect*, not by field."""

    reason: str | None = None


@dataclass(frozen=True)
class PermissionDecision:
    verdict: PermissionVerdict
    reason: str | None = None


HookControl = Union[Allow, Block, Stop, PermissionDecision]


@dataclass
class ResolvedHookOutput:
    """What ``resolve()`` returns — what runtime sites actually consume.

    The channels are **orthogonal to the verdict**: a hook that blocks *and*
    emits a user notice does both. Returning a control alone — which today's
    result objects effectively do — drops ``systemMessage``,
    ``additionalContext``, tool contexts, ``updatedToolOutput`` and diagnostics.
    """

    control: HookControl | None = None
    #: → the human. Not the log: a log line is not a surface the user sees, and
    #: treating one as a contract sink is how ``systemMessage`` came to be
    #: routed into the model's context (§4.3).
    user_notices: list[str] = field(default_factory=list)
    #: → the model's context channel.
    model_contexts: list[str] = field(default_factory=list)
    #: → injected next to a tool result.
    tool_contexts: list[str] = field(default_factory=list)
    updated_tool_input: dict[str, Any] | None = None
    updated_tool_output: Any = None
    #: Warnings, parse failures, budget notices. Never a `hook error` shown to
    #: the user for a field the profile merely declines — that is the author's
    #: correct code, not their mistake.
    diagnostics: list[str] = field(default_factory=list)

    def absorb_channels(self, parsed: ParsedHookOutput, event: str) -> None:
        """Copy the orthogonal channels out of a parse, per the profile's table.

        ``systemMessage`` goes through ``honors_system_message`` exactly as
        ``continue`` goes through ``honors_continue`` — one predicate each, fed
        by §5.1's matrix rather than by a literal in the resolver.
        """
        from ._profile import field_disposition, honors_system_message

        if parsed.universal.system_message and honors_system_message(event):
            self.user_notices.append(parsed.universal.system_message)
        # ``additionalContext`` is defined on six of the eight events, so it is
        # gated on the *field* table rather than delivered everywhere: on
        # ``PreCompact`` and ``SessionEnd`` it is not a field of the contract at
        # all, and routing it there would deliver a channel the reference does
        # not give those events.
        if field_disposition("hookSpecificOutput.additionalContext", event) == "accept":
            self.model_contexts.extend(parsed.additional_context)
