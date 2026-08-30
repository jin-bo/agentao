"""Hook output budget — the two tiers of ``docs/design/hooks-claude-contract-conformance-plan.md`` §6.

**Tier 1 is not here.** It lives at the subprocess boundary
(``capabilities/process.py::run_captured(max_output_bytes=…)``) because that is
the only place a bound can be applied *before* the bytes exist. This module owns
**tier 2**: the semantic cap on the strings a hook contributes to a model
context, a user surface, or the next turn.

Two tiers, because one is not enough. Tier 1 is a *memory* bound, orders of
magnitude above any context budget — it stops a runaway hook from exhausting the
host and does nothing about a hook that prints 200 KB of perfectly well-formed
`additionalContext`. Tier 2 is the context bound, and its unit is the
**channel**, not a named field: exit-2 stderr routed to the model and a `Stop`
reason carried into the next turn are hook-authored strings that reach a model
without passing through any of the three fields the reference happens to name.

Over-budget content is **spilled**, not dropped: the full text is redacted and
written to ``.agentao/hook-outputs/`` and the excerpt points at it, mirroring the
sink ``.agentao/tool-outputs/`` already provides for large tool results. Two
things that sink does *not* do, and this one must (§6.1): the file is created
``0600``, because hook output is a user script's output and likelier to carry
credentials than a tool result; and a write failure is **reported** rather than
swallowed, since a silently lost spill leaves an excerpt claiming a file that
does not exist.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path

from ...security.secret_scan import scan_and_redact

logger = logging.getLogger(__name__)

# --- G4's numbers ---------------------------------------------------------
# Tier 1: the raw ceiling per stream, per hook invocation. Deliberately far
# above any legitimate hook (the largest thing a hook plausibly prints is a
# diff or a log tail) and far below the point where the host is in trouble.
HOOK_RAW_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024

# Tier 2: per channel string. The reference caps hook output strings at 10,000
# characters; characters rather than tokens because a tokenizer here would make
# the bound depend on the configured model, and the budget this protects is the
# context window either way.
HOOK_CHANNEL_CHAR_LIMIT = 10_000

# 20% head / 80% tail: a hook's reason or error tends to be at the end, while
# the opening lines identify what produced it. Same split as the tool sink.
_HEAD_RATIO = 0.2

_HOOK_OUTPUT_DIR = Path(".agentao") / "hook-outputs"

# Cleanup, which the tool sink does not have: without it the directory grows for
# the life of the project. Both bounds are applied on write — there is no daemon.
_SPILL_MAX_AGE_S = 7 * 24 * 3600
_SPILL_MAX_FILES = 200


def _prune(directory: Path) -> None:
    """Best-effort age + count pruning. Never raises: a failed prune must not
    fail the spill it was making room for."""
    try:
        files = sorted(
            (f for f in directory.iterdir() if f.is_file()),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return
    now = time.time()
    for index, path in enumerate(files):
        try:
            too_old = (now - path.stat().st_mtime) > _SPILL_MAX_AGE_S
            if index >= _SPILL_MAX_FILES or too_old:
                path.unlink()
        except Exception:
            continue


def _spill(content: str, *, hook_event: str) -> tuple[str, str | None]:
    """Write ``content`` to the spill sink. Returns ``(pointer, failure)``.

    ``pointer`` is the text appended to the excerpt (empty when the write
    failed); ``failure`` is a diagnostic message, or ``None`` on success.
    """
    try:
        _HOOK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(_HOOK_OUTPUT_DIR, 0o700)
        except OSError:
            pass  # a pre-existing directory the user owns differently is theirs
        _prune(_HOOK_OUTPUT_DIR)

        safe_event = "".join(c if c.isalnum() or c in "-_" else "_" for c in hook_event)
        out_file = _HOOK_OUTPUT_DIR / f"{safe_event}_{int(time.time())}_{uuid.uuid4().hex[:6]}.txt"

        # Redact *before* the bytes land, and create the file 0600 before
        # writing to it: a chmod after the write leaves a window where the
        # full, still-unredacted-by-nobody content is world-readable.
        redacted, hits = scan_and_redact(content)
        fd = os.open(str(out_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(redacted)

        note = (
            "  (credential-shaped strings in the saved copy are replaced with "
            "[REDACTED:<kind>]; the excerpt is verbatim)"
            if hits else ""
        )
        return f"\nFull hook output saved to: {out_file}{note}", None
    except Exception as exc:
        # §6.1: report it. An excerpt that names a file which does not exist is
        # worse than an excerpt that says the save failed.
        return "", f"hook output spill failed: {exc}"


def cap_channel(
    text: str,
    *,
    hook_event: str,
    limit: int = HOOK_CHANNEL_CHAR_LIMIT,
) -> tuple[str, str | None]:
    """Cap one hook-authored channel string. Returns ``(text, diagnostic)``.

    Under the limit the input is returned **unchanged and identical** — callers
    compare identity to decide whether anything happened, the same discipline
    ``strip_unicode_tags`` follows.
    """
    if not isinstance(text, str) or len(text) <= limit:
        return text, None

    head_chars = int(limit * _HEAD_RATIO)
    tail_chars = limit - head_chars
    pointer, failure = _spill(text, hook_event=hook_event)
    omitted = len(text) - limit
    excerpt = (
        f"[Hook output truncated: {len(text):,} chars, showing first "
        f"{head_chars:,} and last {tail_chars:,}.{pointer}]\n\n"
        + text[:head_chars]
        + f"\n\n[… {omitted:,} chars omitted …]\n\n"
        + text[-tail_chars:]
    )
    diagnostic = failure or (
        f"{hook_event} hook output exceeded the {limit:,}-character channel "
        f"budget ({len(text):,} chars); the full text was saved and an excerpt "
        f"delivered"
    )
    return excerpt, diagnostic


def cap_all(
    values: list[str],
    *,
    hook_event: str,
) -> tuple[list[str], list[str]]:
    """``cap_channel`` over a list. Returns ``(capped, diagnostics)``."""
    capped: list[str] = []
    diagnostics: list[str] = []
    for value in values:
        text, diagnostic = cap_channel(value, hook_event=hook_event)
        capped.append(text)
        if diagnostic:
            diagnostics.append(diagnostic)
    return capped, diagnostics
