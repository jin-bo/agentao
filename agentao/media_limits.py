"""Shared bounds for inline image input.

Both entry points that accept images — the CLI ``/image`` staging command
(:mod:`agentao.cli.commands.image`) and the ACP ``session/prompt`` wire
handler (:mod:`agentao.acp.session_prompt`) — enforce the same per-image
byte cap and per-turn image count. Centralizing the limits here keeps the
two from drifting apart (a divergence would let one surface accept an image
the other rejects). Importing this module is cheap (no side effects).

:func:`sniff_image_mime` reads the format from the bytes themselves; today
only ``/image`` uses it (ACP still trusts the client's ``mimeType``).
"""

from __future__ import annotations

#: Maximum decoded size of a single image, in bytes.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

#: Maximum number of images attached to a single turn.
MAX_IMAGES_PER_TURN = 16

#: The image formats every supported wire accepts inline — the ones
#: :func:`sniff_image_mime` recognises. OpenAI documents GIF as
#: non-animated only; frames are not counted here.
SUPPORTED_IMAGE_FORMATS = ("PNG", "JPEG", "GIF", "WEBP")


def sniff_image_mime(data: bytes) -> str | None:
    """Return the MIME type the leading bytes of *data* show, or ``None``.

    Only :data:`SUPPORTED_IMAGE_FORMATS` are recognised, so ``None`` means
    "not an image a provider will accept", whatever the file was called.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
