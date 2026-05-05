"""Canonical helpers for hiding secrets in logs, audit events, and host UIs.

The :class:`PermissionDecisionEvent` projection layer, future ``/provider``
masking, and any host code that surfaces credentials to a debug pane all need
the same shape: show enough characters that the operator can tell which key
they have, mask everything in between, and never expand a short value into
something useful to a shoulder-surfer.

This module is the single place to encode that shape so the policy doesn't
drift across call sites.

Imported separately from :mod:`agentao` so a host can pull just the redaction
primitive without paying for the LLM stack.
"""

from __future__ import annotations

from typing import Optional

__all__ = ["mask_secret"]


def mask_secret(
    value: Optional[str],
    *,
    head: int = 4,
    tail: int = 4,
    floor: int = 12,
    placeholder: str = "(not set)",
) -> str:
    """Mask a credential while keeping enough prefix/suffix for identification.

    Args:
        value: The secret to mask. ``None`` and the empty string both render
            as ``placeholder`` so call sites never have to special-case
            "credential is missing" — the rendered string is always safe to
            print.
        head: Characters preserved at the start. Default 4 matches typical
            provider key prefixes (``sk-`` + one byte of entropy on OpenAI,
            ``AKIA`` on AWS).
        tail: Characters preserved at the end. Default 4 leaves the operator
            enough to compare against a vault entry without leaking entropy.
        floor: Minimum length at which ``head`` and ``tail`` are exposed. Below
            this, the whole value is replaced with asterisks of the same
            length. Default 12 ensures ``head + tail`` (8 chars) cannot reveal
            most of a short token.
        placeholder: Returned verbatim when ``value`` is missing or empty.

    Returns:
        A masked string of the form ``"sk-A...3xZk"`` for sufficiently long
        values, ``"********"`` for short values, or ``placeholder`` when there
        is nothing to mask.
    """
    if not value:
        return placeholder

    if head < 0 or tail < 0 or floor < 0:
        raise ValueError("head, tail, and floor must be non-negative")

    length = len(value)
    if length < floor:
        return "*" * length

    if head + tail >= length:
        # Misconfigured floor: never echo the value.
        return "*" * length

    # ``tail=0`` needs the explicit branch — ``value[-0:]`` is the full
    # string, not an empty slice, so a naive f-string would leak everything.
    return f"{value[:head]}...{value[-tail:]}" if tail else f"{value[:head]}..."
