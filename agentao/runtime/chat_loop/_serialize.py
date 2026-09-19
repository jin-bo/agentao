"""Pure helpers for materializing tool calls + reasoning into history.

Both functions take their inputs and return a value with no reference to
``ChatLoopRunner`` or the agent — they're called from the loop body but
themselves talk only to dict / Pydantic shapes. Lifted out so the runner
file reads as control flow without inline serialization noise.

``_serialize_tool_call`` is also imported directly by
``tests/test_outbound_sanitize.py`` and ``tests/test_tool_name_repair.py``
— preserve the symbol on the package facade.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from ...llm._stream_response import WIRE_CARRIER_KEYS
from ..sanitize import canonicalize_tool_arguments


MAX_REASONING_HISTORY_CHARS = 500  # Truncate reasoning_content in history to ~125 tokens


def _attach_reasoning(msg: Dict[str, Any], reasoning_content: Optional[str]) -> None:
    """Attach a truncated copy of ``reasoning_content`` to ``msg`` (in place).

    No-op when ``reasoning_content`` is ``None``. Truncation cap matches
    the prompt-budget assumption that reasoning shouldn't dominate
    history. The trailing ellipsis flags the truncation to the model.
    """
    if reasoning_content is None:
        return
    stored = reasoning_content[:MAX_REASONING_HISTORY_CHARS]
    if len(reasoning_content) > MAX_REASONING_HISTORY_CHARS:
        stored += "..."
    msg["reasoning_content"] = stored


def _attach_thinking_blocks(msg: Dict[str, Any], assistant_message: Any) -> None:
    """Carry the response's signed thinking blocks onto ``msg`` (in place).

    The second, **untruncated** carrier beside ``reasoning_content``. That one
    is a display copy cut to 500 characters; a signed block has to go back to
    the provider whole, and a truncated one is worse than none — it is
    rejected. Only the ``anthropic-messages`` adapter sets the attribute, so
    this is a no-op on every other wire.

    Called for the two messages that record the model's **own** output: the
    tool-call message and the final response. The four synthetic finals
    (max-iterations, length abort, hook stop, doom loop) are a second assistant
    message built from a response whose blocks were already recorded on the
    first; a signed block is the record of one model output, not of two.

    The answer is type-checked, not merely probed: this codebase substitutes
    ``MagicMock`` responses freely, and a mock answers any attribute.
    """
    # One rule for every wire's carrier (``WIRE_CARRIER_KEYS``): a non-empty
    # list of dicts, copied whole under the name it had on the response.
    for key in WIRE_CARRIER_KEYS:
        blocks = getattr(assistant_message, key, None)
        if (
            isinstance(blocks, list)
            and blocks
            and all(isinstance(block, dict) for block in blocks)
        ):
            msg[key] = copy.deepcopy(blocks)


def _serialize_tool_call(tc, *, logger=None) -> dict:
    """Serialize a tool call object to a dict for conversation history.

    Uses model_dump() to preserve ALL Pydantic extra fields at their correct
    level. This handles Gemini's thought_signature (and similar fields)
    regardless of which level they appear at in the response.

    The ``function.arguments`` string is round-tripped through the repair
    pipeline and re-emitted as canonical compact JSON so downstream API
    proxies receive valid JSON even when the model emitted malformed args.
    """
    if hasattr(tc, "model_dump"):
        entry = tc.model_dump()
    else:
        entry: Dict[str, Any] = {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        }
        thought_sig = getattr(tc.function, "thought_signature", None)
        if thought_sig is None:
            thought_sig = getattr(tc, "thought_signature", None)
        if thought_sig is not None:
            entry["function"]["thought_signature"] = thought_sig

    fn = entry.get("function")
    if isinstance(fn, dict):
        fn["arguments"] = canonicalize_tool_arguments(
            fn.get("arguments", ""),
            tool_name=fn.get("name", "?"),
            logger=logger,
        )
    return entry
