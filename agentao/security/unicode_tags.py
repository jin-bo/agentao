"""Unicode tag-block stripping — invisible-character ("ASCII smuggling") defense.

The Unicode tag block (U+E0000–U+E007F) mirrors ASCII into codepoints that
render as **nothing**. ``chr(0xE0000 + ord(c))`` round-trips through every
tokenizer agentao uses (verified against ``o200k_base``: lossless, ~4 tokens
per hidden character), so a web page, an MCP tool result or a file can carry a
block of instructions that is invisible in the terminal, invisible in a diff,
and fully legible to the model.

That makes this a *model-bound* concern, not only a display one. It is the
same class as the bidi/Trojan-Source controls stripped in
``acp_client/render.py`` — text whose only effect is to make what a human sees
diverge from what actually gets interpreted — and it is deliberately NOT the
same class as the recoverable-but-costly operations the hardline floor leaves
to host policy: ``curl | sh`` is excluded because a user may legitimately mean
it, whereas invisible tag characters have no legitimate model-bound use
outside the one case handled below.

**Why this is not a blind range filter.** The tag block has exactly one
legitimate use: RGI *emoji tag sequences*, which encode subdivision flags as
``U+1F3F4`` + an ISO 3166-2 payload + ``U+E007F``. Filtering the whole range
unconditionally destroys them — 🏴󠁧󠁢󠁳󠁣󠁴󠁿 (Scotland), 🏴󠁧󠁢󠁷󠁬󠁳󠁿 (Wales) and 🏴󠁧󠁢󠁥󠁮󠁧󠁿 (England)
all collapse to a bare 🏴. :func:`strip_unicode_tags` is therefore
*structural*: it preserves a run of tag characters only when it forms a
well-formed sequence, and drops every other tag character.

The residual channel is bounded on two axes, because bounding only the first
bounds nothing: a preserved sequence may carry at most
:data:`_MAX_TAG_PAYLOAD` lowercase-alphanumeric characters behind a
**visible** base emoji, and at most :data:`_MAX_TAG_SEQUENCES` sequences
survive per string. The payload alphabet and length are exactly what real
subdivision codes use, so nothing renderable is lost.

**Scope.** This module is the transform; the boundaries that apply it are
listed in ``CLAUDE.md``. It covers the model-bound copy of tool results,
model output re-entering the runtime, and terminal display — it is not
applied to every string in the process, so do not read a call to it as a
blanket guarantee about unrelated inputs.
"""

from __future__ import annotations

import re

_TAG_MIN = 0xE0000
_TAG_MAX = 0xE007F

#: Compiled range test. The per-character Python scan this replaces ran on
#: every tool result and every assistant field; the neighbouring modules
#: (``secret_scan``, ``sanitize``) use compiled patterns on the same paths.
_TAG_RE = re.compile(r"[\U000E0000-\U000E007F]")

#: WAVING BLACK FLAG — the only tag_base RGI defines. Accepting an arbitrary
#: base would hand an attacker a bypass (prefix any emoji, smuggle freely).
_TAG_BASE = "\U0001F3F4"

#: CANCEL TAG — terminates an emoji tag sequence.
_TAG_TERM = 0xE007F

#: Longest real ISO 3166-2 payload: country 2 + subdivision up to 3, so 5
#: (``gbsct``, ``gbeng``, ``gbwls``; ``usca``/``ustx`` are 4). A cap keeps a
#: preserved sequence from doubling as a smuggling channel, so it must match
#: the stated justification exactly — every extra character is one more
#: character an attacker gets for free.
_MAX_TAG_PAYLOAD = 5

#: Ceiling on how many well-formed sequences a single string may keep.
#:
#: The per-sequence cap alone bounds nothing: chaining N valid sequences
#: yields ``N * _MAX_TAG_PAYLOAD`` hidden characters, which is unbounded in
#: exactly the way this module exists to prevent. Each preserved sequence does
#: cost an attacker a *visible* 🏴, so the channel is never silent — but
#: "conspicuous" is not "bounded". Real prose does not chain subdivision flags
#: past a handful (the three UK nations plus a few US states is already
#: unusual), so past this many we stop preserving and strip the rest.
_MAX_TAG_SEQUENCES = 8


def _is_tag_payload(cp: int) -> bool:
    """True for the tag characters real subdivision codes are built from.

    Tag digits (U+E0030–U+E0039) and lowercase tag letters
    (U+E0061–U+E007A). RGI permits U+E0020–U+E007E structurally, but no valid
    sequence uses anything outside these two runs, so the tighter test costs
    nothing and shrinks the residual channel.
    """
    return 0xE0030 <= cp <= 0xE0039 or 0xE0061 <= cp <= 0xE007A


def has_unicode_tags(text: str) -> bool:
    """True iff *text* contains any codepoint in the tag block.

    Cheap pre-check so the common (clean) path allocates nothing.
    """
    return _TAG_RE.search(text) is not None


def _tag_sequence_end(text: str, base: int) -> int | None:
    """Index just past a well-formed emoji tag sequence starting at *base*.

    ``text[base]`` must be :data:`_TAG_BASE`. Returns ``None`` when what
    follows is not a complete, in-alphabet, length-bounded, terminated
    payload — in which case the caller treats the base as an ordinary
    character and strips the tag characters after it.
    """
    i = base + 1
    payload = 0
    while i < len(text):
        cp = ord(text[i])
        if cp == _TAG_TERM:
            return i + 1 if payload else None
        if payload < _MAX_TAG_PAYLOAD and _is_tag_payload(cp):
            payload += 1
            i += 1
            continue
        return None
    return None


def strip_unicode_tags(text: str) -> str:
    """Remove tag-block characters that are not part of an emoji tag sequence.

    Returns *text* itself (same object) whenever nothing was actually
    dropped, so callers can use identity to detect a no-op. That has to be
    checked against the *result*, not just against the presence of tag
    characters: a string whose every tag character belongs to a legitimate
    flag emoji is unchanged, and reporting it as rewritten would make
    ``_sanitize_str_field`` log a spurious sanitization warning for text
    containing nothing worse than 🏴󠁧󠁢󠁳󠁣󠁴󠁿.
    """
    if not text or not has_unicode_tags(text):
        return text

    out: list[str] = []
    i = 0
    n = len(text)
    kept = 0
    while i < n:
        ch = text[i]
        if ch == _TAG_BASE:
            end = _tag_sequence_end(text, i)
            if end is not None and kept < _MAX_TAG_SEQUENCES:
                out.append(text[i:end])
                kept += 1
                i = end
                continue
        elif _TAG_MIN <= ord(ch) <= _TAG_MAX:
            i += 1  # orphan tag character — drop
            continue
        out.append(ch)
        i += 1
    result = "".join(out)
    return text if result == text else result


def count_unicode_tags(text: str) -> int:
    """Number of tag-block characters :func:`strip_unicode_tags` would drop.

    Used for log lines — an operator needs to see that content was rewritten
    and by how much, since the removal is by definition invisible.
    """
    if not text or not has_unicode_tags(text):
        return 0
    return len(_TAG_RE.findall(text)) - len(_TAG_RE.findall(strip_unicode_tags(text)))
