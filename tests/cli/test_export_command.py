"""``/export`` (#664): the Markdown renderer and the command, on real files."""

from __future__ import annotations

import io
import os
import stat
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from agentao.cli.commands import export as export_cmd
from agentao.cli.session_export import MAX_TOOL_RESULT_CHARS, render_markdown
from agentao.compaction.request import KEPT_REQUEST_NOTE, turn_reminder
from agentao.embedding import save_session

NOW = datetime(2026, 11, 22, 12, 0, 0)


def _ask(text):
    return {"role": "user", "content": turn_reminder(NOW) + text}


HISTORY = [
    {"role": "system", "content": "SYSTEM PROMPT SECRET"},
    _ask("How do I list files?"),
    {
        "role": "assistant", "content": "Let me check.", "reasoning_content": "THINKING",
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "run_shell_command", "arguments": '{"command": "ls"}'}}],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "a.txt\nTOOL OUTPUT"},
    {"role": "user", "content": "<system-reminder>\nBackground agent finished: INJECTED\n</system-reminder>"},
    {"role": "assistant", "content": "Use `ls`."},
]


def _render(messages=HISTORY, **kw):
    return render_markdown(messages, title="T", session_id="sid", now=NOW, **kw)


# ── renderer ────────────────────────────────────────────────────────────────


def test_default_keeps_only_user_and_assistant_text():
    md = _render()
    assert "## User\n\nHow do I list files?" in md
    assert "## Assistant\n\nLet me check." in md and "Use `ls`." in md
    for hidden in ("SYSTEM PROMPT SECRET", "THINKING", "TOOL OUTPUT",
                   "run_shell_command", "INJECTED", "Current Date/Time"):
        assert hidden not in md
    assert md.startswith("# T\n") and "- Session: `sid`" in md


def test_tools_are_included_only_when_asked():
    md = _render(include_tools=True)
    assert "**Tool call:** `run_shell_command`" in md and '"command": "ls"' in md
    assert "**Tool result:** `run_shell_command`" in md and "TOOL OUTPUT" in md
    assert "THINKING" not in md and "INJECTED" not in md


def test_a_long_tool_result_is_capped_and_says_so():
    big = [HISTORY[1], HISTORY[2], {"role": "tool", "tool_call_id": "c1",
                                    "content": "x" * (MAX_TOOL_RESULT_CHARS + 50)}]
    md = _render(big, include_tools=True)
    assert "(50 more characters not exported)" in md


def test_a_compacted_history_says_it_covers_only_what_was_kept():
    compacted = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "system", "content": "[Compact Boundary | tokens_before=1 | auto=True]"},
        {"role": "system", "content": "[Conversation Summary]\nSUMMARY TEXT"},
        {"role": "user", "content": KEPT_REQUEST_NOTE + turn_reminder(NOW) + "keep going"},
        {"role": "assistant", "content": "done"},
    ]
    md = _render(compacted)
    assert "earlier messages were compacted" in md
    assert md.count("compacted here") == 1
    assert "SUMMARY TEXT" not in md and "Compaction removed" not in md
    assert "## User\n\nkeep going" in md
    assert "compacted" not in _render()


def test_the_last_rung_is_marked_by_its_kept_request():
    # The minimal-history rung writes no summary; the kept request is the trace.
    md = _render([
        {"role": "user", "content": KEPT_REQUEST_NOTE + turn_reminder(NOW) + "keep going"},
        {"role": "assistant", "content": "done"},
    ])
    assert "earlier messages were compacted" in md
    assert md.count("compacted here") == 1


def test_multimodal_parts_become_placeholders():
    msg = {"role": "user", "content": [
        {"type": "text", "text": turn_reminder(NOW) + "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "input_audio", "input_audio": {}},
    ]}
    md = _render([msg])
    assert "what is this?" in md and "[image]" in md and "[input_audio attachment]" in md
    assert "base64" not in md


def test_a_fence_outlasts_backticks_in_the_result():
    msgs = [HISTORY[2], {"role": "tool", "tool_call_id": "c1", "content": "````\ninner\n````"}]
    md = _render(msgs, include_tools=True)
    assert "`````\n````\ninner\n````\n`````" in md


def test_an_empty_transcript_says_so():
    assert "No user or assistant messages" in _render([HISTORY[0]])


# ── command ─────────────────────────────────────────────────────────────────


@pytest.fixture
def out(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(export_cmd, "console", Console(file=buf, width=300, color_system=None))
    return buf


def _cli(root: Path, messages=None):
    agent = SimpleNamespace(messages=list(HISTORY if messages is None else messages),
                            working_directory=root, get_current_model=lambda: "m-1")
    return SimpleNamespace(agent=agent, current_session_id="live-session")


def test_writes_the_current_conversation(tmp_path, out):
    export_cmd.handle_export_command(_cli(tmp_path), "chat.md")
    md = (tmp_path / "chat.md").read_text(encoding="utf-8")
    assert md.startswith("# How do I list files?\n")
    assert "- Session: `live-session`" in md and "- Model: `m-1`" in md
    assert "TOOL OUTPUT" not in md
    assert "Exported to" in out.getvalue() and "--tools" in out.getvalue()


def test_an_existing_file_needs_force(tmp_path, out):
    target = tmp_path / "chat.md"
    target.write_text("KEEP", encoding="utf-8")
    export_cmd.handle_export_command(_cli(tmp_path), "chat.md")
    assert target.read_text(encoding="utf-8") == "KEEP"
    assert "already exists. Add --force" in out.getvalue()
    export_cmd.handle_export_command(_cli(tmp_path), "chat.md --force --tools")
    assert "TOOL OUTPUT" in target.read_text(encoding="utf-8")
    assert [p.name for p in tmp_path.iterdir()] == ["chat.md"]  # no temp left


def test_file_errors_are_clear(tmp_path, out):
    export_cmd.handle_export_command(_cli(tmp_path), "missing/chat.md")
    assert "does not exist" in out.getvalue()
    (tmp_path / "d").mkdir()
    export_cmd.handle_export_command(_cli(tmp_path), "d")
    assert "is a directory" in out.getvalue()


def test_the_session_name_is_the_title(tmp_path, out):
    cli = _cli(tmp_path)
    cli._session_name = ("live-session", "Named chat")
    export_cmd.handle_export_command(cli, "chat.md")
    assert (tmp_path / "chat.md").read_text(encoding="utf-8").startswith("# Named chat\n")


def test_a_quoted_path_may_hold_spaces(tmp_path, out):
    export_cmd.handle_export_command(_cli(tmp_path), '"my chat.md"')
    assert (tmp_path / "my chat.md").exists()


@pytest.mark.parametrize("args, says", [
    ("", "Usage: /export"),
    ("a.md b.md", "Only one path"),
    ("a.md --bogus", "Unknown option --bogus"),
    ("a.md --session", "--session needs a session id"),
    ("a.md --session --tools", "--session needs a session id"),
])
def test_bad_arguments_write_nothing(tmp_path, out, args, says):
    export_cmd.handle_export_command(_cli(tmp_path), args)
    assert says in out.getvalue()
    assert list(tmp_path.iterdir()) == []


def test_an_empty_conversation_is_refused(tmp_path, out):
    export_cmd.handle_export_command(_cli(tmp_path, messages=[]), "chat.md")
    assert "Nothing to export" in out.getvalue() and not (tmp_path / "chat.md").exists()


def _save(root, sid, text):
    save_session([_ask(text), {"role": "assistant", "content": f"re {text}"}], "saved-model",
                 session_id=sid, project_root=root)


def test_a_saved_session_by_unique_prefix(tmp_path, out):
    _save(tmp_path, "aaaa1111-0000", "first")
    _save(tmp_path, "aaaa1111-0000", "first again")  # same session, two files
    _save(tmp_path, "bbbb2222-0000", "other")
    export_cmd.handle_export_command(_cli(tmp_path, messages=[]), "s.md --session aaaa")
    md = (tmp_path / "s.md").read_text(encoding="utf-8")
    assert "first again" in md and "other" not in md
    assert "- Session: `aaaa1111-0000`" in md and "- Model: `saved-model`" in md


def test_an_ambiguous_or_unknown_prefix_is_refused(tmp_path, out):
    _save(tmp_path, "abcd1111-0000", "one")
    _save(tmp_path, "abcd2222-0000", "two")
    export_cmd.handle_export_command(_cli(tmp_path), "s.md --session abcd")
    assert "matches 2 sessions" in out.getvalue()
    export_cmd.handle_export_command(_cli(tmp_path), "s.md --session zzzz")
    assert "No saved session matches 'zzzz'" in out.getvalue()
    assert not (tmp_path / "s.md").exists()


def test_only_the_runtimes_prefixes_are_removed_from_a_request():
    hook = "<user-prompt-submit-hook>\nHOOK CONTEXT\n</user-prompt-submit-hook>\n"
    typed = "Explain this tag: <system-reminder>example</system-reminder>"
    md = _render([_ask(hook + typed)])
    assert typed in md
    assert "HOOK CONTEXT" not in md and "Current Date/Time" not in md


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_the_file_gets_a_plain_writes_mode_and_force_keeps_it(tmp_path, out):
    umask = os.umask(0)
    os.umask(umask)
    export_cmd.handle_export_command(_cli(tmp_path), "new.md")
    assert stat.S_IMODE((tmp_path / "new.md").stat().st_mode) == 0o666 & ~umask
    old = tmp_path / "old.md"
    old.write_text("x", encoding="utf-8")
    old.chmod(0o640)
    export_cmd.handle_export_command(_cli(tmp_path), "old.md --force")
    assert stat.S_IMODE(old.stat().st_mode) == 0o640
