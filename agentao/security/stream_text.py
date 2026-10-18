"""Sanitize model text that arrives in pieces.

:func:`agentao.runtime.sanitize.sanitize_text_field` cleans a whole string:
lone surrogates become U+FFFD and invisible tag characters are stripped.
Streamed reasoning reaches a host one piece at a time, before the whole
string exists, and must arrive just as clean — a lone surrogate also makes
the replay writer drop the event (``ensure_ascii=False``).

Two properties make the piecewise result equal the whole-text result:

- **Surrogates are per code point.** A pair split across two pieces is two
  lone code points in the joined string too, so replacing per piece matches.
- **Tag sequences can span pieces.** A piece that ends inside an unfinished
  flag-emoji sequence holds that tail back for the next piece, and the
  :data:`~agentao.security.unicode_tags._MAX_TAG_SEQUENCES` count runs
  across the whole stream.

So ``"".join(feed(p) for p in pieces) + flush()`` equals
``sanitize_text_field("".join(pieces))``. One instance per stream: a retry
starts a new one.
"""

from __future__ import annotations

import re

from .unicode_tags import strip_unicode_tags_counted, unfinished_tag_sequence_start

#: The one surrogate policy: ``runtime/sanitize.py`` imports both, so the
#: streamed and the whole-text result cannot drift apart.
SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
SURROGATE_REPLACEMENT = "\ufffd"


class StreamTextSanitizer:
    """Stateful :func:`sanitize_text_field` for one stream of pieces."""

    def __init__(self) -> None:
        self._held = ""
        self._kept = 0

    def feed(self, piece: str) -> str:
        """Return the clean text that can be shown now (possibly ``""``)."""
        text = self._held + SURROGATE_RE.sub(SURROGATE_REPLACEMENT, piece)
        cut = unfinished_tag_sequence_start(text)
        if cut is None:
            self._held = ""
        else:
            text, self._held = text[:cut], text[cut:]
        out, self._kept = strip_unicode_tags_counted(text, self._kept)
        return out

    def flush(self) -> str:
        """Return what was held back, now that no more pieces will come."""
        out, self._kept = strip_unicode_tags_counted(self._held, self._kept)
        self._held = ""
        return out
