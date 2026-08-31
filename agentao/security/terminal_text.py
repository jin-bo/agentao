"""Terminal-escape sanitization for untrusted text bound for a TTY.

Text that agentao echoes to the operator's terminal does not all come from
agentao. Two provenances matter, and they are the same defect with two
sources:

- **An ACP server** is a third-party subprocess whose notification / agent
  text is echoed verbatim (``acp_client/render.py``; see
  ``docs/design/acp-client-audit.md`` AC5). There it is defense-in-depth —
  the server already runs env-scrubbed (``capabilities/process.py``) — but
  the plain render path writes straight to stdout, so it is the unambiguous
  injection vector on the output channel.
- **The model** authors tool-call arguments, ``ask_user`` questions and
  reasoning text, all of which the reference CLI prints — including at the
  tool-confirmation prompt, where the operator is deciding whether to run
  the very string being displayed.

That second case is why this is a security primitive and not a cosmetic
one: an approval prompt that renders control bytes lets the text being
approved differ from the text being executed. Removing the bytes is only
half the job at a Rich boundary — Rich *markup* (``[black on black]``) also
makes text unreadable without any control byte, and the caller must handle
that with :func:`rich.markup.escape`. This module deliberately does not,
because it must stay importable from core (``security`` is a leaf; see
``tests/test_import_layering.py`` rule 3) and ``rich`` is a CLI dependency.
"""

from __future__ import annotations

from .unicode_tags import strip_unicode_tags

__all__ = ["sanitize_terminal_text"]


# Strip the control bytes that drive ANSI/CSI/OSC escape sequences (cursor,
# screen-clear, set-title, clipboard) before display, keeping only the
# whitespace we intend to render.
_ALLOWED_CONTROL = frozenset({"\n", "\t"})

# Unicode bidirectional / directional-formatting controls. These reorder
# rendered text with NO ESC/CSI/OSC byte (Trojan-Source, CVE-2021-42574): e.g. a
# RIGHT-TO-LEFT OVERRIDE (U+202E) can make "denied" visually read "approved". We
# strip the embedding / override / isolate controls and the standalone direction
# marks — they only affect visual ordering, so removing them shows logical order.
# We deliberately do NOT strip ZWJ/ZWNJ/BOM: those are legitimate in emoji
# sequences and Arabic/Indic scripts, and stripping them would corrupt real text.
_BIDI_CONTROLS = frozenset(
    # LRE RLE PDF LRO RLO         LRI RLI FSI PDI       LRM RLM ALM
    [chr(cp) for cp in (
        0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # embeddings / overrides
        0x2066, 0x2067, 0x2068, 0x2069,          # isolates
        0x200E, 0x200F, 0x061C,                  # standalone direction marks
    )]
)


def _is_disallowed_control(ch: str) -> bool:
    """True if *ch* is a terminal control char we must not echo verbatim."""
    if ch in _ALLOWED_CONTROL:
        return False
    # C0 (incl. ESC/U+001B, which begins every CSI/OSC), DEL, and C1.
    if ch < "\x20" or "\x7f" <= ch <= "\x9f":
        return True
    # Unicode bidi/direction overrides (visual spoofing with no escape byte).
    return ch in _BIDI_CONTROLS


def sanitize_terminal_text(text: str) -> str:
    """Drop terminal control chars from untrusted text before display.

    Removes C0 controls (U+0000–U+001F, including ESC), DEL (U+007F), C1
    controls (U+0080–U+009F), the Unicode bidirectional-override / direction
    controls (Trojan-Source), and invisible tag-block characters
    (U+E0000–U+E007F) that are not part of an emoji tag sequence — preserving
    ``\\n`` / ``\\t``. Printable text and other higher Unicode pass through
    untouched, so Markdown / prose renders unchanged.

    The tag-block pass is the same defense as the bidi one and belongs with
    it: both are text whose only function is to make what the operator sees
    diverge from what is actually there. It cannot be folded into
    ``_is_disallowed_control`` because it is *structural* — whether a tag
    character is legitimate depends on the characters around it (a real
    subdivision-flag emoji is a base plus a payload plus a terminator), so it
    needs a windowed pass rather than a per-character predicate.
    """
    if not text:
        return text
    text = strip_unicode_tags(text)
    if not any(_is_disallowed_control(ch) for ch in text):
        return text
    return "".join(ch for ch in text if not _is_disallowed_control(ch))
