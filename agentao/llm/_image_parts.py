"""What a wire adapter sends in place of an image part it cannot translate.

An image part is in history before the model is ever called, so an adapter
that *raised* on one would raise again on every later request, and the
session would be stuck until ``/clear`` (#485). The part can only come from a
host writing history, or a session file written or edited elsewhere:
``chat(images=...)`` always builds a ``data:`` URL. The adapter therefore
sends this note in the outbound copy instead — history keeps what the host
wrote — and says so in ``agentao.log``.
"""

from __future__ import annotations

import logging
from typing import Any, Tuple

logger = logging.getLogger(__name__)


def image_url_fields(part: Any) -> Tuple[Any, Any]:
    """``(url, detail)`` of an ``image_url`` part, whatever shape it has.

    The OpenAI shape is an object, ``{"url": ..., "detail": ...}``. Some
    callers write the URL as a bare string, and ``_logging.py`` already reads
    that form, so a string is taken as the URL (#613). Any other value gives
    ``(None, None)``, which the adapters send as the unsendable-image note —
    calling ``.get`` on it would raise, on this request and every later one.
    The values are returned unchecked: each wire decides what it can send.
    """
    value = part.get("image_url") if isinstance(part, dict) else None
    if isinstance(value, dict):
        return value.get("url"), value.get("detail")
    if isinstance(value, str):
        return value, None
    return None, None


def log_dropped_detail(wire: str, detail: Any) -> None:
    """Log that a ``detail`` the host wrote was not sent.

    Logged on every request that drops it: the part stays in history. Only
    the value's type and a short prefix go to the log.
    """
    shown = repr(detail[:32]) if isinstance(detail, str) else type(detail).__name__
    logger.warning(
        "%s: image detail %s was not sent; the endpoint default applies",
        wire, shown,
    )


UNSENDABLE_IMAGE_NOTE = (
    "[Image omitted: its URL cannot be sent to the model, so it cannot be viewed.]"
)


def unsendable_image_note(wire: str, reason: str) -> str:
    """Log why an image part was replaced, and return the note to send."""
    logger.warning(
        "%s: an image part was sent as a text note: %s", wire, reason
    )
    return UNSENDABLE_IMAGE_NOTE
