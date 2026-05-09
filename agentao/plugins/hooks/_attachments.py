"""HookAttachmentRecord constructors and the attachment → message adapter.

Kept separate from dispatch logic so hooks/_dispatcher and hooks/_user_turn
can both build attachments without sharing internal dispatcher state.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from ..models import HookAttachmentRecord, PreparedTurnMessage


def _make_attachment(
    attachment_type: str,
    payload: dict[str, Any],
    *,
    hook_name: str,
    hook_event: str,
) -> HookAttachmentRecord:
    return HookAttachmentRecord(
        attachment_type=attachment_type,
        payload=payload,
        hook_name=hook_name,
        hook_event=hook_event,
        tool_use_id="",
        uuid=str(_uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _attachment_to_message(attachment: HookAttachmentRecord) -> PreparedTurnMessage:
    """Convert a HookAttachmentRecord to a PreparedTurnMessage."""
    content_parts: list[str] = [f"[{attachment.attachment_type}]"]
    if attachment.payload:
        for k, v in attachment.payload.items():
            content_parts.append(f"{k}: {v}")
    return PreparedTurnMessage(
        role="user",
        content=" ".join(content_parts),
        is_meta=True,
        source=f"hook:{attachment.hook_name}",
    )
