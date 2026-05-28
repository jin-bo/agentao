"""Tests for ``_SlashCompleter`` — draft-preservation behavior.

Covers the two papercuts that motivated the rewrite:

1. Exact-match arg hint must not insert placeholder text into the buffer
   (would clobber any draft tail after the cursor).
2. Prefix completion of arg-taking commands must add a trailing space so
   ``/ageplease refactor`` completes to ``/agent please refactor``
   instead of the broken ``/agentplease refactor``.
"""

from __future__ import annotations

import pytest

prompt_toolkit = pytest.importorskip("prompt_toolkit")
from prompt_toolkit.document import Document  # noqa: E402

from agentao.cli._utils import _SlashCompleter  # noqa: E402


def _complete(buffer: str, cursor: int | None = None) -> list:
    """Run the completer against ``buffer`` with cursor at ``cursor`` (default: end)."""
    if cursor is None:
        cursor = len(buffer)
    doc = Document(text=buffer, cursor_position=cursor)
    return list(_SlashCompleter().get_completions(doc, complete_event=None))


def _texts(completions) -> list[str]:
    return [c.text for c in completions]


# ---------------------------------------------------------------------------
# Bug 1: exact match yields display-only hint, never inserts placeholder text.
# ---------------------------------------------------------------------------


def test_exact_match_arg_command_yields_display_only_hint():
    completions = _complete("/agent bg")
    assert len(completions) == 1
    c = completions[0]
    assert c.text == ""               # nothing inserted
    assert c.start_position == 0
    assert c.display_meta_text == "arg"
    # ``display`` is FormattedText-like; flatten via ``.display`` raw access.
    display = c.display if isinstance(c.display, str) else "".join(
        seg[1] for seg in c.display
    )
    assert display == "<agent-name> <task>"


def test_exact_match_with_draft_tail_does_not_clobber_buffer():
    """Cursor in middle of buffer, before-cursor exact-matches a hint command."""
    buffer = "/crystallize feedback please refactor X across the codebase"
    cursor = len("/crystallize feedback")
    completions = _complete(buffer, cursor=cursor)
    assert len(completions) == 1
    c = completions[0]
    # If accepted, replaces 0 chars with '' — buffer stays exactly as-is.
    assert c.text == ""
    assert c.start_position == 0


# ---------------------------------------------------------------------------
# Bug 2: prefix completion of arg-taking command appends trailing space
#        unless cursor is already followed by whitespace.
# ---------------------------------------------------------------------------


def test_prefix_completion_arg_command_appends_trailing_space():
    completions = _complete("/age")
    texts = _texts(completions)
    # Both ``/agent`` (no hint → no space) and arg-taking subcommands appear.
    assert "/agent" in texts                        # /agent itself takes no arg
    assert "/agent bg " in texts                    # /agent bg takes args → space
    assert "/agent cancel " in texts                # /agent cancel takes args


def test_prefix_completion_non_arg_command_no_trailing_space():
    completions = _complete("/cle")
    texts = _texts(completions)
    assert "/clear" in texts
    assert "/clear " not in texts                   # no args → no trailing space


def test_prefix_completion_preserves_draft_tail():
    """The classic ``/ageplease refactor`` → ``/agent please refactor`` case.

    Buffer is ``/ageplease refactor`` with cursor at position 4 (after ``/age``).
    Completion should insert ``/agent `` (with trailing space) and replace
    the 4 chars before the cursor — leaving ``please refactor`` untouched.
    """
    buffer = "/ageplease refactor"
    cursor = 4                                      # right after ``/age``
    completions = _complete(buffer, cursor=cursor)
    texts = _texts(completions)
    # /agent bg / cancel / delete / status are the arg-taking ones — all should
    # have a trailing space because text_after = "please refactor" (no leading WS).
    arg_taking = [t for t in texts if t in {
        "/agent bg ", "/agent cancel ", "/agent delete ", "/agent status ",
    }]
    assert arg_taking, f"expected arg-taking subcommands with trailing space, got {texts}"


def test_prefix_completion_skips_trailing_space_if_already_whitespace_after_cursor():
    """If user already typed the space, don't double it up.

    Buffer ``/age please refactor`` with cursor after ``/age`` (position 4).
    text_after starts with a space, so the completer should NOT add another.
    """
    buffer = "/age please refactor"
    cursor = 4
    completions = _complete(buffer, cursor=cursor)
    texts = _texts(completions)
    # Arg-taking subcommands should now appear WITHOUT trailing space.
    assert "/agent bg" in texts
    assert "/agent bg " not in texts
    assert "/agent cancel" in texts
    assert "/agent cancel " not in texts


# ---------------------------------------------------------------------------
# Negative cases.
# ---------------------------------------------------------------------------


def test_non_slash_input_yields_nothing():
    assert _complete("hello world") == []
    assert _complete("") == []


def test_command_without_hint_completes_plainly():
    """``/help`` is not in _SLASH_COMMAND_HINTS — no trailing space."""
    completions = _complete("/hel")
    texts = _texts(completions)
    assert "/help" in texts
    assert "/help " not in texts
