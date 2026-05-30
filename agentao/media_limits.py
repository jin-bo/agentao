"""Shared bounds for inline image input.

Both entry points that accept images — the CLI ``/image`` staging command
(:mod:`agentao.cli.commands.image`) and the ACP ``session/prompt`` wire
handler (:mod:`agentao.acp.session_prompt`) — enforce the same per-image
byte cap and per-turn image count. Centralizing the limits here keeps the
two from drifting apart (a divergence would let one surface accept an image
the other rejects). Importing this module is cheap (no side effects).
"""

from __future__ import annotations

#: Maximum decoded size of a single image, in bytes.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

#: Maximum number of images attached to a single turn.
MAX_IMAGES_PER_TURN = 16
