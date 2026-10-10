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

logger = logging.getLogger(__name__)

UNSENDABLE_IMAGE_NOTE = (
    "[Image omitted: its URL cannot be sent to the model, so it cannot be viewed.]"
)


def unsendable_image_note(wire: str, reason: str) -> str:
    """Log why an image part was replaced, and return the note to send."""
    logger.warning(
        "%s: an image part was sent as a text note: %s", wire, reason
    )
    return UNSENDABLE_IMAGE_NOTE
