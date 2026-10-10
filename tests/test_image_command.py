"""Tests for the /image slash command (Option-B CLI image input)."""

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


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
    # ``expanduser`` reads ``HOME`` on POSIX and ``USERPROFILE`` on Windows, so setting one
    # of them relocates the home directory on one platform and nothing on the other.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
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


def test_image_filename_markup_is_rendered_literally(tmp_path, monkeypatch):
    import io
    from rich.console import Console
    from agentao.cli.commands import image as image_mod

    output = io.StringIO()
    monkeypatch.setattr(image_mod, "console", Console(file=output, color_system=None, width=200))
    image = tmp_path / "[red]shot.png"
    image.write_bytes(_PNG_BYTES)
    cli = _cli()
    handle_image_command(cli, str(image))
    assert image.name in output.getvalue()
    output.seek(0)
    output.truncate(0)
    handle_image_command(cli, "")
    assert image.name in output.getvalue()


def test_missing_image_filename_cannot_close_rich_markup(tmp_path, monkeypatch):
    import io
    from rich.console import Console
    from agentao.cli.commands import image as image_mod

    output = io.StringIO()
    monkeypatch.setattr(image_mod, "console", Console(file=output, color_system=None, width=200))
    image = tmp_path / "[" / "error].png"
    # The path contains [/error], even though neither component exists.
    handle_image_command(_cli(), str(image))
    assert str(image) in output.getvalue()


def test_image_listing_preserves_windows_backslash_before_bracket(monkeypatch):
    import io
    from rich.console import Console
    from agentao.cli.commands import image as image_mod

    output = io.StringIO()
    monkeypatch.setattr(image_mod, "console", Console(file=output, color_system=None, width=200))
    label = r"C:\images\[\error].png"
    cli = _cli()
    cli._staged_images = [{"_label": label, "data": "abcd", "mimeType": "image/png"}]
    handle_image_command(cli, "")
    assert label in output.getvalue()


# Leading bytes of each supported format; the sniffer reads nothing past them.
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_GIF_BYTES = b"GIF89a" + b"\x00" * 16
_WEBP_BYTES = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 16


def _printed(console) -> str:
    return str(console.print.call_args.args[0])


def test_each_supported_format_is_labelled_by_its_bytes(tmp_path):
    cli = _cli()
    for name, data, expected in (
        ("a.png", _PNG_BYTES, "image/png"),
        ("b.jpg", _JPEG_BYTES, "image/jpeg"),
        ("c.gif", _GIF_BYTES, "image/gif"),
        ("d.webp", _WEBP_BYTES, "image/webp"),
    ):
        p = tmp_path / name
        p.write_bytes(data)
        handle_image_command(cli, str(p))
        assert cli._staged_images[-1]["mimeType"] == expected, name
    assert len(cli._staged_images) == 4


def test_misnamed_image_is_labelled_by_content(tmp_path):
    """#464: ``shot.png`` holding JPEG bytes used to go out as image/png."""
    img = tmp_path / "shot.png"
    img.write_bytes(_JPEG_BYTES)
    cli = _cli()
    handle_image_command(cli, str(img))
    assert [i["mimeType"] for i in cli._staged_images] == ["image/jpeg"]


def test_compressed_image_is_refused_with_its_encoding(tmp_path, monkeypatch):
    """#464: a gzip of a PNG named ``fake.png`` used to stage as image/png.

    Covers the ``.png.gz`` naming from #440 too: the bytes decide, whatever
    the suffix."""
    import bz2
    import gzip
    import lzma

    console = Mock()
    monkeypatch.setattr("agentao.cli.commands.image.console", console)
    for name, data, encoding in (
        ("fake.png", gzip.compress(_PNG_BYTES), "gzip"),
        ("shot.png.gz", gzip.compress(_PNG_BYTES), "gzip"),
        ("shot.png.bz2", bz2.compress(_PNG_BYTES), "bzip2"),
        ("shot.png.xz", lzma.compress(_PNG_BYTES), "xz"),
        ("logo.svgz", gzip.compress(b"<svg/>"), "gzip"),
    ):
        p = tmp_path / name
        p.write_bytes(data)
        cli = _cli()
        handle_image_command(cli, str(p))
        assert cli._staged_images == [], name
        message = _printed(console)
        assert f"Compressed file: {p}" in message, name
        assert f"({encoding}; decompress it before attaching)" in message, name


def test_unsupported_image_content_is_refused(tmp_path, monkeypatch):
    """Named like an image, but not one the wires accept inline."""
    console = Mock()
    monkeypatch.setattr("agentao.cli.commands.image.console", console)
    for name, data in (
        ("notes.png", b"hello"),
        ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("old.bmp", b"BM" + b"\x00" * 32),
        ("photo.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16),
        ("riff.webp", b"RIFF\x10\x00\x00\x00WAVEfmt " + b"\x00" * 16),
    ):
        p = tmp_path / name
        p.write_bytes(data)
        cli = _cli()
        handle_image_command(cli, str(p))
        assert cli._staged_images == [], name
        message = _printed(console)
        assert f"Unsupported image content: {p}" in message, name
        assert "(supported: PNG, JPEG, GIF, WEBP)" in message, name


def test_refusal_renders_markup_in_the_name_literally(tmp_path, monkeypatch):
    """The new refusals keep #443's rule: a file name is text, not markup."""
    console = Mock()
    monkeypatch.setattr("agentao.cli.commands.image.console", console)
    p = tmp_path / "[bold]x.png"
    p.write_bytes(b"hello")
    handle_image_command(_cli(), str(p))
    printed = console.print.call_args.args[0]
    assert "[bold]x.png" in printed.plain
    assert all(s.style in ("error", "dim") for s in printed.spans)


def test_webp_is_accepted_by_name_without_a_system_mime_table(tmp_path, monkeypatch):
    """Python < 3.13 has no built-in ``.webp`` entry; a supported format must
    not be refused by the name pre-filter when the system table lacks it."""
    import mimetypes

    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: (None, None))
    p = tmp_path / "d.webp"
    p.write_bytes(_WEBP_BYTES)
    cli = _cli()
    handle_image_command(cli, str(p))
    assert [i["mimeType"] for i in cli._staged_images] == ["image/webp"]


def test_compression_without_magic_is_named_from_the_suffix(tmp_path, monkeypatch):
    """Brotli has no signature; ``shot.png.br`` is still refused as compressed."""
    console = Mock()
    monkeypatch.setattr("agentao.cli.commands.image.console", console)
    p = tmp_path / "shot.png.br"
    p.write_bytes(b"\x0b\x02\x80hello\x03")
    cli = _cli()
    handle_image_command(cli, str(p))
    assert cli._staged_images == []
    assert "(br; decompress it before attaching)" in _printed(console)


def test_encoding_suffix_over_uncompressed_bytes_is_not_called_compressed(tmp_path, monkeypatch):
    """Only brotli is read from the name; a ``.gz`` holding plain bytes is
    unsupported content, not a file to decompress."""
    console = Mock()
    monkeypatch.setattr("agentao.cli.commands.image.console", console)
    p = tmp_path / "shot.png.gz"
    p.write_bytes(b"BM" + b"\x00" * 32)
    cli = _cli()
    handle_image_command(cli, str(p))
    assert cli._staged_images == []
    assert f"Unsupported image content: {p}" in _printed(console)
