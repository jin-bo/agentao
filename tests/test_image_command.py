"""Tests for the /image slash command (Option-B CLI image input)."""

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentao.cli.commands import handle_image_command


# A 1x1 transparent PNG.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9"
    "awAAAABJRU5ErkJggg=="
)


def _cli():
    return SimpleNamespace(_staged_images=[])


def test_stage_image_appends_base64_and_mime(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(_PNG_BYTES)
    cli = _cli()

    handle_image_command(cli, str(img))

    assert len(cli._staged_images) == 1
    staged = cli._staged_images[0]
    assert staged["mimeType"] == "image/png"
    assert base64.b64decode(staged["data"]) == _PNG_BYTES
    assert staged["_label"] == "shot.png"


def test_stage_multiple_images_accumulate(tmp_path):
    cli = _cli()
    for name in ("a.png", "b.png"):
        p = tmp_path / name
        p.write_bytes(_PNG_BYTES)
        handle_image_command(cli, str(p))
    assert len(cli._staged_images) == 2


def test_clear_discards_staged(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(_PNG_BYTES)
    cli = _cli()
    handle_image_command(cli, str(img))
    assert cli._staged_images

    handle_image_command(cli, "clear")
    assert cli._staged_images == []


def test_missing_file_is_rejected_not_staged():
    cli = _cli()
    handle_image_command(cli, "/no/such/file.png")
    assert cli._staged_images == []


def test_non_image_file_is_rejected(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    cli = _cli()
    handle_image_command(cli, str(txt))
    assert cli._staged_images == []


def test_zero_byte_image_is_rejected(tmp_path):
    """An empty file must not stage an empty-data block (which would build a
    malformed `data:image/png;base64,` URL)."""
    empty = tmp_path / "empty.png"
    empty.touch()  # 0 bytes
    cli = _cli()
    handle_image_command(cli, str(empty))
    assert cli._staged_images == []


def test_tilde_and_quotes_are_handled(tmp_path, monkeypatch):
    img = tmp_path / "home.png"
    img.write_bytes(_PNG_BYTES)
    monkeypatch.setenv("HOME", str(tmp_path))
    cli = _cli()

    # Quoted path with ~ should resolve and stage.
    handle_image_command(cli, '"~/home.png"')
    assert len(cli._staged_images) == 1
    assert cli._staged_images[0]["_label"] == "home.png"


def test_filename_with_apostrophe_is_preserved(tmp_path):
    """A real filename containing an apostrophe must not be mangled by
    quote-stripping (only a *matched* surrounding pair is stripped)."""
    img = tmp_path / "it's a shot.png"
    img.write_bytes(_PNG_BYTES)
    cli = _cli()

    # Unquoted path with an interior apostrophe — must stage verbatim.
    handle_image_command(cli, str(img))
    assert len(cli._staged_images) == 1
    assert cli._staged_images[0]["_label"] == "it's a shot.png"


def test_trailing_apostrophe_filename_not_stripped(tmp_path):
    """A trailing apostrophe (legal on POSIX) is not a matched pair, so it
    must be kept rather than stripped into a non-existent path."""
    img = tmp_path / "shot'.png"
    img.write_bytes(_PNG_BYTES)
    cli = _cli()

    handle_image_command(cli, str(img))
    assert len(cli._staged_images) == 1
    assert cli._staged_images[0]["_label"] == "shot'.png"


def test_too_large_image_rejected(tmp_path, monkeypatch):
    from agentao.cli.commands import image as image_mod

    # Shrink the cap so we can test rejection without writing a 20MB file —
    # and prove the size check happens before the file is read into memory.
    monkeypatch.setattr(image_mod, "_MAX_IMAGE_BYTES", 10)
    big = tmp_path / "huge.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)  # 58 bytes > 10
    cli = _cli()
    handle_image_command(cli, str(big))
    assert cli._staged_images == []


def test_toctou_truncated_after_stat_is_rejected(tmp_path, monkeypatch):
    """A file that passes the stat() size check but reads back empty (truncated
    between stat and read) must not stage a malformed empty-data block."""
    img = tmp_path / "race.png"
    img.write_bytes(_PNG_BYTES)  # non-empty at stat() time
    cli = _cli()

    # Simulate truncation-to-zero happening after the stat() guard.
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"")
    handle_image_command(cli, str(img))
    assert cli._staged_images == []


def test_toctou_grown_after_stat_is_rejected(tmp_path, monkeypatch):
    """A file that passes the stat() size check but reads back oversized (grew
    between stat and read) must be rejected by the post-read size guard."""
    from agentao.cli.commands import image as image_mod

    img = tmp_path / "race.png"
    img.write_bytes(_PNG_BYTES)
    cli = _cli()

    monkeypatch.setattr(image_mod, "_MAX_IMAGE_BYTES", 64)
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"\x00" * 128)  # > 64
    handle_image_command(cli, str(img))
    assert cli._staged_images == []


def test_staged_image_cap_enforced(tmp_path):
    from agentao.cli.commands import image as image_mod

    img = tmp_path / "x.png"
    img.write_bytes(_PNG_BYTES)
    cli = _cli()
    # Pre-fill to the cap, then one more must be refused.
    cli._staged_images = [{"data": "x", "mimeType": "image/png"}] * image_mod._MAX_STAGED_IMAGES
    handle_image_command(cli, str(img))
    assert len(cli._staged_images) == image_mod._MAX_STAGED_IMAGES


def _run_loop_once(commands):
    """Drive run_loop with a mocked CLI through the given command lines.

    Returns the mocked cli so callers can assert post-run state. The final
    command must be /exit so the loop terminates.
    """
    from agentao.cli.input_loop import run_loop

    cli = Mock()
    cli._staged_images = [{"data": "QUJD", "mimeType": "image/png"}]
    cli._plan_session.is_active = False
    cli._get_user_input.side_effect = list(commands)
    run_loop(cli)
    return cli


def test_clear_command_resets_staged_images():
    """/clear must drop staged images so they don't leak into a new session."""
    cli = _run_loop_once(["/clear", "/exit"])
    assert cli._staged_images == []


def test_new_command_resets_staged_images():
    """/new must drop staged images so they don't leak into the fresh session."""
    cli = _run_loop_once(["/new", "/exit"])
    assert cli._staged_images == []
