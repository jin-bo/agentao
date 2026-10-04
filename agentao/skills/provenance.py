"""Where MCP skill content sits in a transcript (docs/design/mcp-skills.md).

An MCP skill's instructions enter the conversation through three tools —
``activate_skill`` (``<mcp-skill>``), ``read_skill_file`` and a verified
``read_mcp_resource`` (``<mcp-skill-file>``) — and can be copied onward by
compaction, which folds a window into a ``[Conversation Summary]``. The result
formatter never spills them to ``.agentao/tool-outputs/``
(``_is_mcp_skill_content``), so no spilled file holds one: a ``read_file`` of
a spill is ordinary output.

The gates that make such content safe to act on are session state (held
entries), and a restored or summarised transcript outlives that state. This
module is how the two places that cross that line — compaction and a session
restore — recognise the content. Standard library only: ``context_manager``
imports it, and must not pull in the MCP SDK.
"""

from __future__ import annotations

import html
import re
from typing import Any, Iterable, Set
from urllib.parse import quote, unquote

#: The tools that put a loaded MCP skill's content into the transcript.
MCP_SKILL_TOOLS = frozenset({"activate_skill", "read_skill_file", "read_mcp_resource"})

#: A complete wrapper, with its server label.
BLOCK = re.compile(r"<(mcp-skill|mcp-skill-file)\b([^>]*)>.*?</\1>", re.DOTALL)
#: Any opening or closing wrapper tag, complete or not.
TAG = re.compile(r"</?mcp-skill(?:-file)?\b")
#: A fragment of a wrapper agentao wrote: an opening tag carrying its
#: ``server`` attribute, or a closing tag (what survives of a wrapper whose
#: head was cut). A bare mention — "use the ``<mcp-skill>`` wrapper" in a
#: local skill or an ordinary resource — is neither, so it is not read as
#: skill content of unknown origin.
WRAPPER = re.compile(r'<mcp-skill(?:-file)?\b[^>]*\bserver="|</mcp-skill(?:-file)?>')

#: The header the result formatter puts on a truncated result, which keeps
#: the result's head — so the start of a skill result survives truncation.
_TRUNCATED = re.compile(r"\A\[Output truncated:[^\n]*\]\n\n")
#: How each tool's result starts when it carries a loaded skill: an MCP
#: activation names its ``mcp:`` skill first (a prefix local skills cannot
#: take), and a verified resource read is its wrapper. Anywhere else in the
#: text, a tag is quoted — a design doc, a skill that documents the format.
_SKILL_RESULT_START = {
    "activate_skill": re.compile(r"\s*Skill Activated: mcp:"),
    "read_mcp_resource": re.compile(r'\s*<mcp-skill-file server="'),
    # Every byte of content the tool returns is wrapped; anything else — its
    # refusals, a binary note, the runtime's stand-in for a call denied,
    # declined, cancelled or raised — carries none.
    "read_skill_file": re.compile(r'\s*<mcp-skill-file server="'),
}


def is_skill_result(tool_name: Any, content: Any) -> bool:
    """Whether a result of ``tool_name`` carries a loaded MCP skill's content.

    By the tool and by where the result starts, never by a tag anywhere in it
    — and never by listing the shapes that are *not* content: a result that
    carries none (an error, a denial) would otherwise read as content of an
    unknown origin (``*``) and gate the conversation that merely saw it.
    """
    start = _SKILL_RESULT_START.get(tool_name)
    if start is None or not isinstance(content, str):
        return False
    return bool(start.match(_TRUNCATED.sub("", content, count=1)))


_SERVER_ATTR = re.compile(r'<mcp-skill(?:-file)?\b[^>]*\bserver="([^"]*)"')

#: The line compaction adds to a summary of MCP skill content. ``*`` names
#: content whose server could not be told.
SUMMARY_MARKER = "[This summary includes content from MCP skills served by: {labels}]"
#: The same, on a sub-agent's result returned to its parent.
RESULT_MARKER = "[This result includes content from MCP skills served by: {labels}]"
_SUMMARY_MARKER = re.compile(
    r"\[This (?:summary|result) includes content from MCP skills served by: ([^\]]*)\]"
)
#: The same, as the runtime writes it: a whole line. A marker quoted inside a
#: sentence or a string literal is not one.
_MARKER_LINE = re.compile(
    r"^\[This (?:summary|result) includes content from MCP skills served by: ([^\]\n]*)\]$",
    re.MULTILINE,
)
#: The templates' own placeholder: labels are percent-encoded when written,
#: so ``{labels}`` raw is the source text quoted, never a written marker.
_TEMPLATE_LABELS = "{labels}"
UNKNOWN_ORIGIN = "*"

#: The opening of the placeholder a restore writes over withheld skill
#: content (``embedding/sessions.py::withheld``). Here so the marker reader
#: below can tell it apart without importing ``embedding``.
WITHHELD_PREFIX = "[MCP skill content withheld:"


_PLACEHOLDER_MARKER = re.compile(
    r"^" + re.escape(WITHHELD_PREFIX) + r"[^\n]*\n(\[This (?:summary|result) includes "
    r"content from MCP skills served by: [^\]\n]*\])$",
    re.MULTILINE,
)


def carries_marker(tool_name: Any) -> bool:
    """Whether a tool's result may carry an origin marker of ours.

    Only a sub-agent's answer does (``agent_*``, ``check_background_agent``).
    Every other tool returns text someone else wrote — a file, a web page,
    a grep, an MCP result — and the marker phrase in it means nothing:
    trusting it would withhold an ordinary read, or gate the session under
    a label nobody configured.
    """
    return isinstance(tool_name, str) and (
        tool_name.startswith("agent_") or tool_name == "check_background_agent"
    )


def marker_origins(message: Any) -> Set[str]:
    """Origins named by a trusted marker on one transcript message.

    A marker counts on any message the runtime or the model wrote, on a
    sub-agent tool's result (:func:`carries_marker`), and on a restore's own
    placeholder. On any other tool result it is content, not provenance.
    A tool message with no ``name`` (an older save) is trusted: wrongly
    trusting costs a confirmation, wrongly ignoring loses a gate.
    """
    if not isinstance(message, dict):
        return set()
    content = message.get("content")
    if message.get("role") == "assistant":
        # The model's own words: the runtime writes no marker there, so one
        # is quoted text (explaining this module, say), never provenance.
        return set()
    if message.get("role") == "tool" and "name" in message:
        if not carries_marker(message.get("name")):
            # Only a restore's own placeholder: the marker line directly
            # under it (a withheld activation keeps its head line above).
            # A restore writes one only over a skill tool's result; the same
            # two lines in a ``read_file`` or web result are quoted text.
            if not isinstance(content, str) or message.get("name") not in MCP_SKILL_TOOLS:
                return set()
            return summary_origins(
                "\n".join(m.group(1) for m in _PLACEHOLDER_MARKER.finditer(content))
            )
    return summary_origins(content)


def is_skill_message(message: Any) -> bool:
    """Whether this tool message carries MCP skill content (by provenance)."""
    if not isinstance(message, dict) or message.get("role") != "tool":
        return False
    return is_skill_result(message.get("name"), message.get("content"))


def summary_origins(text: Any) -> Set[str]:
    """Server labels a summary's or a sub-agent result's marker names."""
    if not isinstance(text, str):
        return set()
    labels: Set[str] = set()
    for match in _MARKER_LINE.finditer(text):
        if match.group(1).strip() == _TEMPLATE_LABELS:
            continue
        decoded = {unquote(part.strip()) for part in match.group(1).split(",") if part.strip()}
        # A marker is never empty when written; one that reads as empty was
        # damaged, and still means "MCP skill content, server unknown".
        labels.update(decoded or {UNKNOWN_ORIGIN})
    return labels


def strip_markers(text: str) -> str:
    """``text`` with every origin marker (summary or result form) removed.

    For text copied somewhere the marker would be trusted although the copy
    lost what made it trustworthy — a tool result excerpted into a
    sub-agent's ``user`` message. The caller re-adds a fresh marker for the
    origins :func:`skill_origins` trusts on the original message.
    """
    # Until nothing is left: one pass can splice a marker together from the
    # text around a removed one (``[This result … skills [<marker>]served by:
    # x]``), and the caller trusts what comes back.
    while True:
        stripped = _SUMMARY_MARKER.sub("", text)
        if stripped == text:
            return text
        text = stripped


def _encode(labels: Iterable[str]) -> str:
    """Labels for a marker: percent-encoded, so ``]`` and ``,`` round-trip."""
    return ", ".join(quote(label, safe="") for label in sorted(set(labels)))


def skill_origins(messages: Iterable[Any]) -> Set[str]:
    """Server labels of every MCP skill content in ``messages``.

    Includes the labels a carried-over summary marker names, and
    :data:`UNKNOWN_ORIGIN` for content whose server is not written on it
    (an unwrapped ``read_skill_file`` result).
    """
    messages = list(messages)
    labels: Set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        marked = marker_origins(message)
        labels |= marked
        if not is_skill_message(message):
            continue
        content = message.get("content")
        found = [html.unescape(f) for f in _SERVER_ATTR.findall(content)] if isinstance(content, str) else []
        # A withheld activation keeps its head line but not its wrapper: the
        # placeholder's marker names the server instead.
        labels.update(found or marked or [UNKNOWN_ORIGIN])
    return labels


def content_origins(content: Any) -> Set[str]:
    """Server labels written on the wrappers in ``content``, or ``{"*"}``."""
    found = {html.unescape(f) for f in _SERVER_ATTR.findall(content)} if isinstance(content, str) else set()
    return found or {UNKNOWN_ORIGIN}


def name_origin(name: Any) -> Any:
    """The server label of an ``mcp:<label>:<uri>`` skill name, or ``None``.

    Without the list of configured labels: the URI starts at its scheme,
    which holds no ``:``, so the label is everything before the ``:`` that
    precedes ``<scheme>://``. Anything else ``mcp:``-prefixed reads as an
    unknown origin — it named an MCP skill all the same.
    """
    if not isinstance(name, str) or not name.startswith("mcp:"):
        return None
    rest = name[len("mcp:"):]
    scheme_end = rest.find("://")
    colon = rest.rfind(":", 0, scheme_end) if scheme_end > 0 else -1
    return rest[:colon] if colon > 0 else UNKNOWN_ORIGIN


def summary_marker(labels: Iterable[str]) -> str:
    return SUMMARY_MARKER.format(labels=_encode(labels))


def result_marker(labels: Iterable[str]) -> str:
    return RESULT_MARKER.format(labels=_encode(labels))

