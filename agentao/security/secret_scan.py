"""Pattern-based secret scanning for anything agentao writes down.

A small, ordered set of regexes over a string; matches become
``[REDACTED:<kind>]`` and a per-kind counter comes back so callers can
report how much fired.

Distinct from :func:`agentao.redact.mask_secret`, which masks a credential
the caller *already holds* (``sk-A...3xZk``). This module is the other
direction: find credentials inside text nobody vetted — command output,
an LLM payload — and blank them before that text is persisted.

Scope — read this before assuming a sink is covered. Two call sites, and
only two:

* ``agentao.log`` (``llm/client.py::_RedactingFormatter``)
* ``.agentao/tool-outputs/`` (``runtime/tool_result_formatter.py``)

It is deliberately *not* applied to the tool result handed to the model.
Patterns cannot tell a live credential from a test fixture, and mangling
``sk-test-…`` in a file the agent is actively editing breaks legitimate
work in a way the agent can neither see nor fix.

That exclusion has a consequence worth stating plainly rather than
implying coverage that does not exist: because the in-context copy stays
verbatim, any sink that persists **conversation history** also persists
whatever the model saw. Those sinks are *not* scanned —
``.agentao/sessions/*.json`` (``embedding/sessions.py::save_session``)
and ``.agentao/background_tasks.json`` (``agents/bg_store.py``) both
serialize message/result text as-is. That is a deliberate trade, not an
oversight: both round-trip back into ``agent.messages`` on resume, so
redacting them would silently corrupt a resumed conversation the same way
redacting the live path would corrupt a live one. Redaction is applied
where the output is terminal, withheld where it is re-read.

So the honest statement of the guarantee: agentao's **append-only
diagnostic artifacts** are scrubbed. Conversation state is not, and
containment of what leaves the machine remains a host concern.

Lives under ``security/`` rather than ``replay/`` because the replay
subsystem is optional and off by default, while these call sites are not.
``agentao.replay.redact`` re-exports the names for its own callers.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple


# Ordered from most specific to most general so a JWT or private-key
# block isn't partially eaten by a later, looser pattern.
SECRET_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]+KEY-----[\s\S]+?-----END [A-Z ]+KEY-----"
        ),
    ),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{40,}")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b")),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"
        ),
    ),
    (
        # Two shapes, same ``bearer`` kind: (1) a standalone ``Bearer <tok>``
        # token and (2) an ``Authorization: [Bearer ]<tok>`` header. The
        # original single-pattern form failed to strip the ``Bearer ``
        # literal before the token, so ``Authorization: Bearer <tok>``
        # slipped through unredacted.
        "bearer",
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=+/]{20,}"),
    ),
    (
        "bearer",
        re.compile(
            r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?[A-Za-z0-9_\-\.=+/]{20,}"
        ),
    ),
    (
        # Inline key=value / key: value pairs. Uses a negative lookbehind on
        # word chars so "xapi_key=..." doesn't match (false-positive-heavy
        # outside of secret contexts). The quoted value is captured to keep
        # the key visible in the redacted output for readability.
        "kv_secret",
        re.compile(
            r"(?i)(?<![A-Za-z0-9_])(api[_-]?key|token|secret|password|passwd)"
            r"\s*[:=]\s*[\"']?([^\s\"']{8,})[\"']?"
        ),
    ),
]


# Strings shorter than this cannot possibly contain any of the tokens
# above (shortest real match is the ``AKIA...`` 20-char AWS key). Short
# strings skip the regex loop — a cheap win for the many small string
# fields (tool names, statuses, call ids, etc.) that flow through every
# event.
_MIN_SCAN_LEN = 20


def scan_and_redact(text: str) -> Tuple[str, Dict[str, int]]:
    """Return ``(redacted_text, hits_by_kind)`` for *text*.

    ``hits_by_kind`` counts how many times each pattern fired.  An empty
    dict means the string was already clean. Never raises — callers on
    the logging and disk-write paths must not be able to fail here.
    """
    if not isinstance(text, str) or len(text) < _MIN_SCAN_LEN:
        return text, {}
    hits: Dict[str, int] = {}
    for kind, pattern in SECRET_PATTERNS:
        # ``search`` first so a clean string costs one pass per pattern and
        # no substitution machinery. Most strings are clean.
        if pattern.search(text) is None:
            continue

        def _sub(_match, _kind: str = kind) -> str:
            hits[_kind] = hits.get(_kind, 0) + 1
            return f"[REDACTED:{_kind}]"

        text = pattern.sub(_sub, text)
    return text, hits


def redact(text: str) -> str:
    """``scan_and_redact`` without the counters, for call sites that only
    need the cleaned string (logging formatters, disk writes)."""
    cleaned, _hits = scan_and_redact(text)
    return cleaned


__all__ = ["SECRET_PATTERNS", "scan_and_redact", "redact"]
