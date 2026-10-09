"""``/image`` slash command — stage image attachments for the next turn.

Option-B image input: the user runs ``/image <path>`` (repeatably) to
attach one or more images, then types their message normally. The staged
images are consumed by the next ``agent.chat(..., images=...)`` call in
``input_loop`` and surfaced to the LLM as OpenAI ``image_url`` parts.

Subcommands:

- ``/image <path>``  — stage an image file (base64-encoded inline).
- ``/image``         — list currently staged images.
- ``/image clear``   — discard all staged images.

Only PNG, JPEG, GIF and WEBP are accepted — the formats every supported
wire takes inline. The file name is a pre-filter only (its
:func:`mimetypes.guess_type` type must start with ``image/``); the MIME
type that is staged comes from the file's leading bytes
(:func:`agentao.media_limits.sniff_image_mime`), so a misnamed or
compressed file is refused instead of staged with a wrong label. Errors
(missing file, non-image, unsupported format, unreadable) are surfaced,
never silently swallowed.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from rich.text import Text
from typing import TYPE_CHECKING

from ...media_limits import (
    MAX_IMAGE_BYTES as _MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_TURN as _MAX_STAGED_IMAGES,
    SUPPORTED_IMAGE_FORMATS,
    sniff_image_mime,
)
from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI

# Compression signatures, checked only to word the refusal: a ``.png.gz``
# is an image the user can fix by decompressing it.
_COMPRESSION_MAGIC = (
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"\x1f\x9d", "compress"),
)


def _compression_of(data: bytes) -> str | None:
    for magic, name in _COMPRESSION_MAGIC:
        if data.startswith(magic):
            return name
    return None


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _show_staged(cli: "AgentaoCLI") -> None:
    staged = cli._staged_images
    if not staged:
        console.print("\n[info]No images staged.[/info] "
                      "Use [cyan]/image <path>[/cyan] to attach one.\n")
        return
    console.print(f"\n[info]{len(staged)} image(s) staged for the next message:[/info]")
    for i, img in enumerate(staged, 1):
        # data is base64; approximate the decoded payload size for display.
        approx = (len(img["data"]) * 3) // 4
        label = img.get("_label", "image")
        console.print(Text.assemble(
            f"  {i}. {label}  ",
            (f"({img['mimeType']}, ~{_format_size(approx)})", "dim"),
        ))
    console.print("[dim]They will be sent with your next message. /image clear to discard.[/dim]\n")


def handle_image_command(cli: "AgentaoCLI", args: str) -> None:
    """Handle ``/image`` and its subcommands. Mutates ``cli._staged_images``."""
    args = args.strip()

    if not args:
        _show_staged(cli)
        return

    if args.lower() == "clear":
        count = len(cli._staged_images)
        cli._staged_images = []
        console.print(f"\n[green]✓ Cleared {count} staged image(s).[/green]\n")
        return

    # Treat the remainder as a single path (supports a "~" prefix and a
    # *matched* surrounding quote pair). Only a matched pair is stripped, so
    # a real filename with a leading/trailing quote or apostrophe (e.g.
    # ``it's.png``) is preserved. Spaces in unquoted paths are kept verbatim.
    raw = args.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        raw = raw[1:-1]
    path = Path(raw).expanduser()

    if not path.exists():
        console.print(Text(f"\nNo such file: {path}\n", style="error"))
        return
    if not path.is_file():
        console.print(Text(f"\nNot a file: {path}\n", style="error"))
        return

    # The name is a cheap pre-filter only, so ``notes.txt`` is refused before
    # it is read; the staged type comes from the bytes below.
    named_type, named_encoding = mimetypes.guess_type(str(path))
    if named_type is None and path.suffix.lower() == ".webp":
        # Python's built-in table learned ``.webp`` only in 3.13; without a
        # system mime.types entry a supported format would be refused here.
        named_type = "image/webp"
    if named_type is None or not named_type.startswith("image/"):
        console.print(Text.assemble(
            (f"\nNot a recognized image file: {path} ", "error"),
            (f"(got {named_type or 'unknown type'})\n", "dim"),
        ))
        return

    if len(cli._staged_images) >= _MAX_STAGED_IMAGES:
        console.print(
            f"\n[error]Already {_MAX_STAGED_IMAGES} images staged "
            f"(the per-message limit).[/error] "
            f"[dim]Send them or run /image clear first.[/dim]\n"
        )
        return

    # Reject oversized files by stat() *before* reading them — a multi-GB
    # file would otherwise be loaded into memory just to be rejected.
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        console.print(Text(f"\nCould not stat {path}: {exc}\n", style="error"))
        return

    if file_size == 0:
        console.print(Text(f"\nImage file is empty: {path}\n", style="error"))
        return

    if file_size > _MAX_IMAGE_BYTES:
        console.print(
            f"\n[error]Image too large: {_format_size(file_size)}[/error] "
            f"[dim](limit {_MAX_IMAGE_BYTES // (1024 * 1024)} MB)[/dim]\n"
        )
        return

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        console.print(Text(f"\nCould not read {path}: {exc}\n", style="error"))
        return

    # Re-validate the bytes actually read — the stat() above is a separate
    # syscall, so a file truncated to empty (→ malformed `data:;base64,`
    # block) or grown past the cap between stat and read would otherwise slip
    # through. Trust the bytes in hand, not the earlier stat.
    if not raw_bytes:
        console.print(Text(f"\nImage file is empty: {path}\n", style="error"))
        return
    if len(raw_bytes) > _MAX_IMAGE_BYTES:
        console.print(
            f"\n[error]Image too large: {_format_size(len(raw_bytes))}[/error] "
            f"[dim](limit {_MAX_IMAGE_BYTES // (1024 * 1024)} MB)[/dim]\n"
        )
        return

    # Label the image by what its bytes are, not what it is called: a
    # ``shot.png`` holding JPEG bytes, or a gzip of a PNG, would otherwise go
    # out under a wrong media type and the provider would reject the request.
    mime_type = sniff_image_mime(raw_bytes)
    if mime_type is None:
        # Brotli has no magic number, so for it alone fall back to what the
        # name says (``shot.png.br`` → ``br``). Every other encoding has a
        # signature: a ``.gz`` name over bytes that are not gzip is not a
        # compressed file, and must not be told to decompress.
        compression = _compression_of(raw_bytes) or (
            "br" if named_encoding == "br" else None
        )
        if compression is not None:
            console.print(Text.assemble(
                (f"\nCompressed file: {path} ", "error"),
                (f"({compression}; decompress it before attaching)\n", "dim"),
            ))
        else:
            console.print(Text.assemble(
                (f"\nUnsupported image content: {path} ", "error"),
                (f"(supported: {', '.join(SUPPORTED_IMAGE_FORMATS)})\n", "dim"),
            ))
        return

    data = base64.b64encode(raw_bytes).decode("ascii")
    cli._staged_images.append({
        "data": data,
        "mimeType": mime_type,
        "_label": path.name,
        "_source": str(path),
    })
    console.print(Text.assemble(
        (f"\n✓ Staged image: {path.name} ", "green"),
        (f"({mime_type}, {_format_size(len(raw_bytes))}) — "
         f"{len(cli._staged_images)} total, sent with your next message.\n", "dim"),
    ))
