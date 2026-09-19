"""The one history id a Responses function call is kept under.

Its own module, below every adapter: the ``openai-responses`` adapter composes
and splits the id, and the Chat Completions adapter — whose request is held
byte-identical to a pre-extraction capture — has to recognise one to send it
as a ``call_id``. Importing that from the Responses adapter would put the
default wire downstream of a module it has nothing else to do with.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

#: One history slot, two wire ids. See :func:`compose_tool_id`.
_ID_SEPARATOR = "|"
#: The prefix OpenAI gives a function-call *item* id. :func:`split_tool_id`
#: reads the part after the separator as an item id only when it has it.
_ITEM_ID_PREFIX = "fc_"


def compose_tool_id(call_id: str, item_id: Optional[str]) -> str:
    """The one id agentao's history keeps for a Responses function call.

    The wire has two: ``call_id`` correlates the output, and the item ``id``
    names the call item itself. History has one slot, and that id must
    round-trip byte for byte — a second key would have to survive sanitize,
    compaction, replay and session load, and ``tool_call_id`` is what the
    compaction pairing rules match on. So: ``call_id|item_id``.
    """
    if isinstance(item_id, str) and item_id.startswith(_ITEM_ID_PREFIX):
        return f"{call_id}{_ID_SEPARATOR}{item_id}"
    return call_id


def split_tool_id(tool_id: Any) -> Tuple[str, Optional[str]]:
    """``(call_id, item_id)`` back out of a history id.

    Split on the **last** separator, and only when what follows looks like an
    item id. History outlives a provider switch, so the id may have been
    minted on another wire: one that merely contains ``|`` stays whole, since
    a mis-split would send half of it as the ``call_id`` and the other half as
    an item id the API never issued.
    """
    text = tool_id if isinstance(tool_id, str) else ""
    head, sep, tail = text.rpartition(_ID_SEPARATOR)
    if sep and head and tail.startswith(_ITEM_ID_PREFIX):
        return head, tail
    return text, None
