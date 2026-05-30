"""``/image`` slash command — stage image attachments for the next turn.

Option-B image input: the user runs ``/image <path>`` (repeatably) to
attach one or more images, then types their message normally. The staged
images are consumed by the next ``agent.chat(..., images=...)`` call in
``input_loop`` and surfaced to the LLM as OpenAI ``image_url`` parts.

Subcommands:

- ``/image <path>``  — stage an image file (base64-encoded inline).
- ``/image``         — list currently staged images.
- ``/image clear``   — discard all staged images.

Only image files are accepted — the MIME type is inferred from the
extension via :func:`mimetypes.guess_type` and must start with
``image/``. Errors (missing file, non-image, unreadable) are surfaced,
never silently swallowed.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING

from ...media_limits import (
    MAX_IMAGE_BYTES as _MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_TURN as _MAX_STAGED_IMAGES,
)
from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


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
        console.print(f"  {i}. {label}  [dim]({img['mimeType']}, ~{_format_size(approx)})[/dim]")
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
        console.print(f"\n[error]No such file: {path}[/error]\n")
        return
    if not path.is_file():
        console.print(f"\n[error]Not a file: {path}[/error]\n")
        return

    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type is None or not mime_type.startswith("image/"):
        console.print(
            f"\n[error]Not a recognized image file: {path}[/error] "
            f"[dim](got {mime_type or 'unknown type'})[/dim]\n"
        )
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
        console.print(f"\n[error]Could not stat {path}: {exc}[/error]\n")
        return

    if file_size == 0:
        console.print(f"\n[error]Image file is empty: {path}[/error]\n")
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
        console.print(f"\n[error]Could not read {path}: {exc}[/error]\n")
        return

    # Re-validate the bytes actually read — the stat() above is a separate
    # syscall, so a file truncated to empty (→ malformed `data:;base64,`
    # block) or grown past the cap between stat and read would otherwise slip
    # through. Trust the bytes in hand, not the earlier stat.
    if not raw_bytes:
        console.print(f"\n[error]Image file is empty: {path}[/error]\n")
        return
    if len(raw_bytes) > _MAX_IMAGE_BYTES:
        console.print(
            f"\n[error]Image too large: {_format_size(len(raw_bytes))}[/error] "
            f"[dim](limit {_MAX_IMAGE_BYTES // (1024 * 1024)} MB)[/dim]\n"
        )
        return

    data = base64.b64encode(raw_bytes).decode("ascii")
    cli._staged_images.append({
        "data": data,
        "mimeType": mime_type,
        "_label": path.name,
        "_source": str(path),
    })
    console.print(
        f"\n[green]✓ Staged image: {path.name}[/green] "
        f"[dim]({mime_type}, {_format_size(len(raw_bytes))}) — "
        f"{len(cli._staged_images)} total, sent with your next message.[/dim]\n"
    )
