"""Tests for ``_SlashCompleter`` — draft-preservation behavior.

Covers the two papercuts that motivated the rewrite:

1. Exact-match arg hint must not insert placeholder text into the buffer
   (would clobber any draft tail after the cursor).
2. Prefix completion of arg-taking commands must add a trailing space so
   ``/ageplease refactor`` completes to ``/agent please refactor``
   instead of the broken ``/agentplease refactor``.
3. The exact-match hint must not *suppress* subcommand completions — see
   the ``hint_does_not_hide_subcommands`` tests below.
"""

from __future__ import annotations

import pytest

prompt_toolkit = pytest.importorskip("prompt_toolkit")
from prompt_toolkit.document import Document  # noqa: E402

from agentao.cli._utils import _SLASH_COMMANDS, _SLASH_COMMAND_HINTS, _SlashCompleter  # noqa: E402


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


# ---------------------------------------------------------------------------
# Bug 3: the exact-match hint must not suppress subcommand completions.
#
# ``get_completions`` used to ``return`` straight after yielding the hint. Any
# command that is BOTH hint-bearing AND a prefix of its own subcommands
# therefore offered the hint and nothing else. Only ``/goal`` and ``/image``
# have that shape, which is why the breakage stayed invisible next to
# ``/mcp`` and ``/replay`` — those carry no hint entry, so they never took the
# early return.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", ["/goal", "/image"])
def test_hint_does_not_hide_subcommands(cmd):
    """Typing the bare command must still offer every subcommand."""
    expected = {c for c in _SLASH_COMMANDS if c.startswith(cmd + " ")}
    assert expected, f"{cmd} has no subcommands — pick a different fixture command"

    texts = _texts(_complete(cmd))
    # Completion text carries a trailing space for arg-taking subcommands.
    offered = {t.rstrip() for t in texts if t}
    missing = expected - offered
    assert not missing, f"{cmd} + Tab hid these subcommands: {sorted(missing)}"


@pytest.mark.parametrize("cmd", ["/goal", "/image"])
def test_hint_still_shown_alongside_subcommands(cmd):
    """The arg hint is additive, not replaced by the subcommand list."""
    completions = _complete(cmd)
    hints = [c for c in completions if c.text == "" and c.start_position == 0]
    assert len(hints) == 1, f"expected exactly one display-only hint for {cmd}"
    display = hints[0].display if isinstance(hints[0].display, str) else "".join(
        seg[1] for seg in hints[0].display
    )
    assert display == _SLASH_COMMAND_HINTS[cmd]


@pytest.mark.parametrize("cmd", ["/goal", "/image"])
def test_hint_command_not_re_offered_as_its_own_completion(cmd):
    """The hint already describes the bare command; don't also insert it."""
    texts = _texts(_complete(cmd))
    assert cmd not in texts
    assert cmd + " " not in texts


def test_hintless_command_subcommands_unaffected():
    """Regression guard for the commands that always worked (``/mcp``)."""
    expected = {c for c in _SLASH_COMMANDS if c.startswith("/mcp ")}
    offered = {t.rstrip() for t in _texts(_complete("/mcp")) if t}
    assert expected <= offered
