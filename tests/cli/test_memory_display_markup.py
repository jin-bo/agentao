"""Regression guard: ``/memory`` output must survive bracketed content.

Memory titles, contents and tags are written by the LLM (``save_memory``) and
routinely contain square brackets — remembered filesystem paths, quoted rich /
BBCode tags, ``[TODO]``-style markers. Both display paths interpolate those
values into a rich markup string, so an unescaped ``[/...]`` raises
``rich.errors.MarkupError`` *mid-loop*: the header prints, some entries print,
and the rest — including the tag summary — is silently dropped. A user
auditing what the agent has stored gets a truncated answer with no indication
that anything is missing.

Two paths, both covered here because they are separate code with the same bug:

- ``show_memories._print_entry`` — ``/memory``, ``list``, ``search``, ``tag``
- ``_utils._display_layered_entries`` — ``/memory user``, ``/memory project``

The layered helper additionally takes ``console_``. That parameter exists so a
host or test can redirect output by swapping ``console`` on the *handler*
module; ``test_layered_view_renders_into_the_injected_console`` pins it,
because a lint fix that deleted the parameter would silently send entries to
the process's real stdout while the surrounding chrome went to the sink.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("rich")
from rich.console import Console  # noqa: E402

from agentao.cli._utils import _display_layered_entries  # noqa: E402
from agentao.cli.commands_ext import memory as memory_cmd  # noqa: E402
from agentao.memory.models import MemoryRecord  # noqa: E402


# Each of these aborts rich's markup parser, or silently vanishes from the
# output, when interpolated unescaped.
HOSTILE = [
    "path [/etc/agentao] here",      # closing tag with no opener → MarkupError
    "see [/usr/bin]",                # same shape, different payload
    "styled [bold] text",            # valid opener → swallowed + leaks style
    "[TODO] follow up",              # unknown style → error at render
]


def _rec(title: str, content: str, tags=(), scope="project") -> MemoryRecord:
    return MemoryRecord(
        id="mem-1",
        scope=scope,
        type="fact",
        key_normalized="k",
        title=title,
        content=content,
        tags=list(tags),
        updated_at="2026-08-03T00:00:00Z",
    )


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    # force_terminal=False keeps the capture free of ANSI codes; markup
    # parsing (the thing under test) happens either way.
    return Console(file=buf, width=200, force_terminal=False), buf


# ---------------------------------------------------------------------------
# /memory, /memory list — show_memories._print_entry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", HOSTILE)
def test_memory_list_survives_bracketed_content(payload, monkeypatch):
    """A hostile entry must not abort the listing or hide the entries after it."""
    entries = [
        _rec("first", payload),
        _rec("second", "plain content"),
    ]

    class _Mgr:
        def get_all_entries(self, **kwargs):
            return entries

    class _Agent:
        memory_manager = _Mgr()

    class _Cli:
        agent = _Agent()

    console, buf = _console()
    monkeypatch.setattr(memory_cmd, "console", console)

    memory_cmd.show_memories(_Cli(), "list")

    out = buf.getvalue()
    # The whole listing completed: both entries AND the trailing tag summary
    # section boundary. Before the fix, `second` never printed.
    assert "first" in out
    assert "second" in out, f"listing truncated after the hostile entry:\n{out}"
    # The literal text survives rather than being eaten as a style tag.
    assert payload in out, f"payload was swallowed by the markup parser:\n{out}"


@pytest.mark.parametrize("field", ["title", "tags"])
def test_memory_list_survives_bracketed_title_and_tags(field, monkeypatch):
    """Not just content — titles and tags are LLM-written too."""
    if field == "title":
        entries = [_rec("cfg [/etc/x]", "plain"), _rec("second", "plain")]
        needle = "cfg [/etc/x]"
    else:
        entries = [_rec("first", "plain", tags=["a[/b]"]), _rec("second", "plain")]
        needle = "a[/b]"

    class _Cli:
        class agent:
            class memory_manager:
                @staticmethod
                def get_all_entries(**kwargs):
                    return entries

    console, buf = _console()
    monkeypatch.setattr(memory_cmd, "console", console)

    memory_cmd.show_memories(_Cli(), "list")

    out = buf.getvalue()
    assert needle in out
    assert "second" in out, f"listing truncated:\n{out}"


# ---------------------------------------------------------------------------
# /memory user, /memory project — _display_layered_entries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", HOSTILE)
def test_layered_view_survives_bracketed_content(payload):
    entries = [_rec("first", payload), _rec("second", "plain content")]
    console, buf = _console()

    _display_layered_entries(entries, "[Profile Memory]", console)

    out = buf.getvalue()
    assert "first" in out
    assert "second" in out, f"layered view truncated:\n{out}"
    assert payload in out


def test_layered_view_header_brackets_are_literal():
    """The header is passed as ``[Profile Memory]`` and means it literally."""
    console, buf = _console()
    _display_layered_entries([_rec("t", "c")], "[Profile Memory]", console)
    assert "[Profile Memory]" in buf.getvalue()


def test_layered_view_empty_header_brackets_are_literal():
    """The no-entries branch renders the same header — escape it there too."""
    console, buf = _console()
    _display_layered_entries([], "[Project Memory]", console)
    assert "[Project Memory]" in buf.getvalue()


def test_layered_view_renders_into_the_injected_console():
    """``console_`` is an injection point, not decoration.

    ``/memory user`` calls this from ``commands_ext/memory.py``, passing that
    module's ``console``. A host or test that swaps the attribute there must
    capture the *entries*, not just the chrome — which is what deleting the
    parameter (and falling back to ``_utils``' own module-level import) would
    have quietly broken.
    """
    console, buf = _console()
    _display_layered_entries([_rec("unique-title", "unique-content")], "[H]", console)
    out = buf.getvalue()
    assert "unique-title" in out
    assert "unique-content" in out


def test_layered_view_defaults_to_module_console_when_omitted():
    """Two-argument callers keep working — the parameter is optional."""
    import agentao.cli._utils as utils_mod

    console, buf = _console()
    original = utils_mod.console
    utils_mod.console = console
    try:
        _display_layered_entries([_rec("defaulted", "body")], "[H]")
    finally:
        utils_mod.console = original
    assert "defaulted" in buf.getvalue()


# ---------------------------------------------------------------------------
# Every other display boundary in the command.
#
# The first pass at this fix escaped only ``_print_entry`` and the layered
# view; the tag-summary loop still crashed the listing, which is how this
# section came to exist. A display-boundary sanitizer has to cover *every*
# boundary — escaping the obvious one just moves the crash.
# ---------------------------------------------------------------------------


def _cli_with(entries):
    class _Cli:
        class agent:
            class memory_manager:
                @staticmethod
                def get_all_entries(**kwargs):
                    return entries

                @staticmethod
                def search(q):
                    return entries

                @staticmethod
                def filter_by_tag(t):
                    return entries

    return _Cli()


def test_tag_summary_survives_bracketed_tag(monkeypatch):
    """The ``#tag`` roll-up at the end of ``/memory list``."""
    entries = [_rec("first", "plain", tags=["a[/b]", "ok"])]
    console, buf = _console()
    monkeypatch.setattr(memory_cmd, "console", console)

    memory_cmd.show_memories(_cli_with(entries), "list")

    out = buf.getvalue()
    assert "Tag Summary" in out, f"crashed before the tag summary:\n{out}"
    assert "#a[/b]" in out


@pytest.mark.parametrize("sub", ["search", "tag"])
def test_query_echo_survives_bracketed_argument(sub, monkeypatch):
    """The user's own query is echoed back — and users type brackets."""
    console, buf = _console()
    monkeypatch.setattr(memory_cmd, "console", console)

    memory_cmd.show_memories(_cli_with([_rec("hit", "plain")]), sub, "[/weird]")

    out = buf.getvalue()
    assert "[/weird]" in out
    assert "hit" in out, f"result list never rendered:\n{out}"


@pytest.mark.parametrize("sub", ["search", "tag"])
def test_empty_result_echo_survives_bracketed_argument(sub, monkeypatch):
    """Same echo on the no-results branch, which is a different string."""

    class _Cli:
        class agent:
            class memory_manager:
                @staticmethod
                def search(q):
                    return []

                @staticmethod
                def filter_by_tag(t):
                    return []

    console, buf = _console()
    monkeypatch.setattr(memory_cmd, "console", console)

    memory_cmd.show_memories(_Cli(), sub, "[/weird]")

    assert "[/weird]" in buf.getvalue()


def test_delete_not_found_echo_survives_bracketed_key(monkeypatch):
    class _Cli:
        class agent:
            class memory_manager:
                @staticmethod
                def delete_by_title(a):
                    return 0

                @staticmethod
                def get_all_entries(**kwargs):
                    return []

    console, buf = _console()
    monkeypatch.setattr(memory_cmd, "console", console)

    memory_cmd.show_memories(_Cli(), "delete", "[/nope]")

    assert "[/nope]" in buf.getvalue()


def test_memory_user_subcommand_output_is_capturable(monkeypatch):
    """End-to-end: the documented redirect idiom captures the entries.

    Swapping ``console`` on the handler module is how this repo captures CLI
    output (``patch.object(acp_mod, "console", ...)`` in
    tests/test_acp_client_cli.py). This asserts the layered branch honours it.
    """
    entries = [_rec("profile-entry", "profile-body", scope="user")]

    class _Cli:
        class agent:
            class memory_manager:
                @staticmethod
                def get_all_entries(**kwargs):
                    return entries

    console, buf = _console()
    monkeypatch.setattr(memory_cmd, "console", console)

    memory_cmd.show_memories(_Cli(), "user")

    out = buf.getvalue()
    assert "profile-entry" in out, f"entries bypassed the injected console:\n{out}"
    assert "profile-body" in out
