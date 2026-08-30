"""Profile-shaped stdin payloads — the input field matrix of §5.3.

One rule governs every cell: **a field agentao cannot source is absent or
documented, never fabricated — and a field upstream does not define is not sent
at all.** Flattening the envelope and renaming keys is the easy layer; the hard
one is that several fields have no value to serialize, and a plan cannot promise
a payload it cannot fill.

The shapes here were checked against six payloads captured from a real
`claude` 2.1.251 (`docs/reference/hooks-probe-2.1.251.md` §F), which confirmed
the matrix — `permission_mode` present on four events and absent on
`SessionStart` / `SessionEnd`, `prompt_id` absent before the first user input,
`agent_id` / `agent_type` absent everywhere — and settled the shape of the two
rows the document could only describe.

**Three deliberate absences**, each a G7 decision recorded rather than hidden:

* ``transcript_path`` is sent as an explicit ``null``. agentao has no
  continuously-written transcript — sessions are saved at save points, replays
  exist only when replay is on — and a path to a file whose contents lag the
  session is worse than a null a hook can branch on. It is ``null`` rather than
  absent because the reference makes it required on all eight events, so a hook
  doing ``payload["transcript_path"]`` would raise instead of branching.
* ``prompt_id`` is **omitted**. agentao has a per-turn id, but the reference
  gives ``turn_id`` to a different event, and reusing one for the other invents
  a correlation that does not hold.
* ``permission_mode`` is mapped where a mapping exists and **omitted otherwise**.
  ``plan`` → ``plan`` is exact and ``full-access`` → ``bypassPermissions`` is
  near-exact; ``workspace-write`` is **not** ``acceptEdits`` and ``read-only``
  has no upstream analogue at all. Sending agentao's own vocabulary is what the
  old code did, and a hook branching on the documented values matched no arm.

  **In practice the field is absent everywhere today**, and saying so is the
  point of the rule: only ``build_stop`` takes a posture at all, and the live
  posture lives on ``AgentaoCLI`` while the tool events dispatch from
  ``tool_runner`` / ``tool_executor``, which never receive it. Plumbing it is a
  later step; until then agentao omits a field it cannot source rather than
  filling it, which is the same rule ``prompt_id`` and ``transcript_path``
  follow. :data:`EVENTS_WITH_PERMISSION_MODE` describes where it *belongs*, not
  where it currently appears.
"""

from __future__ import annotations

from typing import Any

#: agentao's permission postures → the reference's vocabulary. Absent keys are
#: omitted from the payload rather than coerced onto a near-miss.
PERMISSION_MODE_MAP: dict[str, str] = {
    "plan": "plan",
    "full-access": "bypassPermissions",
}

#: ``effort`` is shaped ``{"level": …}`` and only for levels upstream defines.
#: agentao also accepts ``minimal`` and ``off``, neither of which exists there —
#: and coercing ``off`` into a level would tell a hook that thinking is on.
EFFORT_LEVELS: frozenset[str] = frozenset({"low", "medium", "high", "xhigh", "max"})

#: Events the reference gives ``permission_mode``. Sending it on ``PreCompact``
#: — which agentao did — is the forbidden direction of the same rule.
EVENTS_WITH_PERMISSION_MODE: frozenset[str] = frozenset({
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop",
})


def _common(event: str, session_id: str, cwd: str, permission_mode: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": session_id,
        "transcript_path": None,
        "cwd": cwd,
        "hook_event_name": event,
    }
    if event in EVENTS_WITH_PERMISSION_MODE:
        mapped = PERMISSION_MODE_MAP.get(permission_mode or "")
        if mapped is not None:
            payload["permission_mode"] = mapped
    return payload


def to_profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert an agentao-shaped payload into the profile's flat snake_case one.

    Accepts both internal shapes — the ``{event, data}`` envelope and the flat
    ``Stop`` / ``PreCompact`` payloads — because agentao has both today. The
    output is a single shape: **no dual-shape payloads**, since emitting both
    field sets would be a third contract and the matcher would have to guess
    which the author meant.
    """
    event = payload.get("event") or payload.get("hook_event_name") or ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    flat = {} if data else payload

    session_id = str(data.get("sessionId") or flat.get("session_id") or "")
    cwd = str(data.get("cwd") or flat.get("cwd") or "")
    permission_mode = flat.get("permission_mode") or data.get("permissionMode")

    out = _common(event, session_id, cwd, permission_mode)

    if event == "SessionStart":
        out["source"] = data.get("source", "startup")
        if data.get("model"):
            out["model"] = data["model"]
    elif event == "SessionEnd":
        out["reason"] = data.get("reason", "other")
    elif event == "UserPromptSubmit":
        out["prompt"] = data.get("userMessage", "")
    elif event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        out["tool_name"] = data.get("toolName", "")
        out["tool_input"] = data.get("toolInput", {})
        out["tool_use_id"] = data.get("toolUseId", "")
        if event == "PostToolUse":
            # A **string**, where upstream passes the tool's structured output
            # object. agentao's tools return `str` and declare no output schema,
            # so wrapping it in an invented object would be a third contract and
            # emitting the string is a documented type divergence (§5.3).
            out["tool_response"] = data.get("toolOutput", "")
        if event == "PostToolUseFailure":
            out["error"] = data.get("error", "")
            if data.get("isInterrupt") is not None:
                out["is_interrupt"] = bool(data["isInterrupt"])
        if data.get("durationMs") is not None:
            out["duration_ms"] = data["durationMs"]
    elif event == "Stop":
        out["stop_hook_active"] = bool(flat.get("stop_hook_active", False))
        out["last_assistant_message"] = flat.get("last_assistant_message", "")
        # `turn_end_reason` is agentao's own field, forbidden here.
        # `background_tasks` / `session_crons` name features agentao does not
        # have; upstream sends them present-and-empty, and sending [] would
        # claim a feature exists and is idle.
    elif event == "PreCompact":
        out["trigger"] = flat.get("trigger", "")
        out["custom_instructions"] = flat.get("custom_instructions", "")
        # `compaction_type`, `reason` and `permission_mode` are agentao-private
        # here and are dropped: three private fields rode on a flat
        # Claude-shaped payload, which is the forbidden column of the matrix.

    return out


def effort_field(reasoning_effort: str | None) -> dict[str, Any] | None:
    """``{"level": …}`` when the configured effort is one upstream defines."""
    if reasoning_effort in EFFORT_LEVELS:
        return {"level": reasoning_effort}
    return None
