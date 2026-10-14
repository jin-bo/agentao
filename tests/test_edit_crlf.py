"""``replace`` on a CRLF file keeps it CRLF (codex #51203).

The model writes LF. In a file whose every line ends in CRLF, a multi-line
``old_text`` missed the exact tier and matched only in the flexible one, and
``new_text`` was spliced in with bare LFs: the file came out with mixed
endings. Now both are converted to CRLF first. Only a pure-CRLF file is
converted; a file with mixed endings has no single ending to follow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentao.tools.file_ops import _EDIT_SUFFIX_FLEXIBLE, EditTool


@pytest.fixture
def tool(tmp_path):
    t = EditTool()
    t.working_directory = tmp_path.resolve()
    return t


def _write(tmp_path: Path, data: bytes) -> Path:
    p = tmp_path / "f.txt"
    p.write_bytes(data)
    return p


def test_a_multi_line_edit_in_a_crlf_file_stays_crlf(tmp_path, tool):
    p = _write(tmp_path, b"one\r\ntwo\r\nthree\r\n")

    out = tool.execute(file_path="f.txt", old_text="one\ntwo", new_text="ONE\nTWO\nextra")

    assert p.read_bytes() == b"ONE\r\nTWO\r\nextra\r\nthree\r\n"
    assert out == "Replaced 1 occurrence(s) in f.txt"      # the exact tier, not flexible


def test_replace_all_in_a_crlf_file(tmp_path, tool):
    p = _write(tmp_path, b"a\r\nb\r\na\r\nb\r\n")

    tool.execute(file_path="f.txt", old_text="a\nb\n", new_text="x\ny\n", replace_all=True)

    assert p.read_bytes() == b"x\r\ny\r\nx\r\ny\r\n"


def test_the_flexible_tier_in_a_crlf_file_stays_crlf(tmp_path, tool):
    p = _write(tmp_path, b"one   \r\ntwo\r\nthree\r\n")

    out = tool.execute(file_path="f.txt", old_text="one\ntwo", new_text="1\n2")

    assert p.read_bytes() == b"1\r\n2\r\nthree\r\n"
    assert out.endswith(_EDIT_SUFFIX_FLEXIBLE)


def test_crlf_already_in_new_text_is_not_doubled(tmp_path, tool):
    p = _write(tmp_path, b"one\r\ntwo\r\n")

    tool.execute(file_path="f.txt", old_text="one\r\n", new_text="1\r\n1b\n")

    assert p.read_bytes() == b"1\r\n1b\r\ntwo\r\n"


def test_an_lf_file_is_unchanged(tmp_path, tool):
    p = _write(tmp_path, b"one\ntwo\n")

    tool.execute(file_path="f.txt", old_text="one\n", new_text="1\n1b\n")

    assert p.read_bytes() == b"1\n1b\ntwo\n"


def test_a_mixed_file_is_left_alone(tmp_path, tool):
    p = _write(tmp_path, b"one\r\ntwo\nthree\r\n")

    tool.execute(file_path="f.txt", old_text="two", new_text="2\n2b")

    assert p.read_bytes() == b"one\r\n2\n2b\nthree\r\n"


def test_a_file_without_line_endings_is_left_alone(tmp_path, tool):
    p = _write(tmp_path, b"single line")

    tool.execute(file_path="f.txt", old_text="single", new_text="a\nb")

    assert p.read_bytes() == b"a\nb line"


def test_the_not_found_hint_sees_the_models_own_text(tmp_path, tool, monkeypatch):
    _write(tmp_path, b"one\r\ntwo\r\n")
    seen = []
    monkeypatch.setattr(EditTool, "_not_found_hint", lambda self, c, o, f: seen.append(o) or "x")

    tool.execute(file_path="f.txt", old_text="nope\nmissing", new_text="y")

    assert seen == ["nope\nmissing"]
