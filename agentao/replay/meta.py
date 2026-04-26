"""ReplayMeta — summary describing one replay instance file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ReplayMeta:
    """Summary for ``/replays`` listings and reader navigation."""

    session_id: str
    instance_id: str
    path: Path
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    event_count: int = 0
    turn_count: int = 0
    has_errors: bool = False
    malformed_lines: int = 0
    first_user_message: Optional[str] = None

    @property
    def full_id(self) -> str:
        return f"{self.session_id}.{self.instance_id}"

    @property
    def short_id(self) -> str:
        short_session = self.session_id[:8]
        return f"{short_session}.{self.instance_id[:6]}"
