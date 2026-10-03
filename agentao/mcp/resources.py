"""MCP resources: the manager's data shapes, and how they reach the model.

``McpClientManager.list_resources`` / ``list_resource_templates`` /
``read_resource`` return the dataclasses below — plain data, never files, and
nothing tied to a session (docs/design/mcp-resources.md §5.1). Everything that
needs a session — where a binary is saved, whether a read hint names a tool
the model can call — is decided by the caller and passed in here.

Also home to :func:`render_call_result`, the one renderer for a ``tools/call``
result (§6): ``McpClient.call_tool`` uses it with no ``save``, ``McpTool``
with ``save`` bound to its working directory.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
import uuid
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ._compat import field

#: Every blob is checked against this before it is decoded (opencode's cap).
#: The 40,000-character spill happens after decoding, so it cannot bound the
#: decode's memory; this does.
MAX_BLOB_BYTES = 10 * 1024 * 1024

#: Saved binaries share ``tool-outputs/`` and its 7-day pruning; the fixed,
#: generated prefix is what the pruner's glob matches.
SAVED_RESOURCE_PREFIX = "mcp-resource_"

#: The all-servers listing walk reuses ``tools/list``'s bounds, per server.
MAX_LIST_PAGES = 100
MAX_LIST_ITEMS = 1024
MAX_CURSOR_BYTES = 64 * 1024

#: Blob types decoded as UTF-8 text rather than saved (pi's list).
_TEXT_MIME_EXACT = ("application/json", "application/xml", "application/javascript")
_TEXT_MIME_SUFFIX = ("+json", "+xml")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceInfo:
    """One ``resources/list`` entry. ``_meta``, icons and annotations dropped."""

    server: str
    uri: str
    name: str
    title: Optional[str] = None
    description: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None


@dataclass(frozen=True)
class ResourceTemplateInfo:
    """One ``resources/templates/list`` entry."""

    server: str
    uri_template: str
    name: str
    title: Optional[str] = None
    description: Optional[str] = None
    mime_type: Optional[str] = None


@dataclass(frozen=True)
class ResourcePage:
    server: str
    resources: List[ResourceInfo]
    next_cursor: Optional[str] = None


@dataclass(frozen=True)
class TemplatePage:
    server: str
    templates: List[ResourceTemplateInfo]
    next_cursor: Optional[str] = None


@dataclass(frozen=True)
class ResourceContent:
    """One item of a read: text, or a still-encoded base64 ``blob``."""

    uri: str
    mime_type: Optional[str] = None
    text: Optional[str] = None
    blob: Optional[str] = None


@dataclass(frozen=True)
class ResourceRead:
    server: str
    uri: str
    contents: List[ResourceContent] = dc_field(default_factory=list)


class McpResourceError(RuntimeError):
    """A resource request that produced no data.

    ``kind`` is one of ``unknown_server``, ``disabled``, ``unsupported`` (the
    connection declares no ``resources``), ``connection``, ``auth``,
    ``not_found``, ``input_required``, ``catalog`` (a paging bound) or
    ``error``. The message is written for the model and the user alike.
    """

    def __init__(self, server: str, kind: str, message: str):
        super().__init__(message)
        self.server = server
        self.kind = kind


# ---------------------------------------------------------------------------
# SDK model → data
# ---------------------------------------------------------------------------


def _opt_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def resource_info(server: str, model: Any) -> ResourceInfo:
    return ResourceInfo(
        server=server,
        uri=str(model.uri),
        name=model.name,
        title=getattr(model, "title", None),
        description=model.description,
        mime_type=field(model, "mimeType", "mime_type"),
        size=getattr(model, "size", None),
    )


def template_info(server: str, model: Any) -> ResourceTemplateInfo:
    return ResourceTemplateInfo(
        server=server,
        uri_template=str(field(model, "uriTemplate", "uri_template")),
        name=model.name,
        title=getattr(model, "title", None),
        description=model.description,
        mime_type=field(model, "mimeType", "mime_type"),
    )


def resource_content(model: Any) -> ResourceContent:
    return ResourceContent(
        uri=str(model.uri),
        mime_type=field(model, "mimeType", "mime_type"),
        text=getattr(model, "text", None),
        blob=getattr(model, "blob", None),
    )


def is_mcp_app(uri: str, mime_type: Optional[str]) -> bool:
    """An MCP Apps (``ui://``) resource — only a host that renders them can use one."""
    if uri.startswith("ui://"):
        return True
    mime = (mime_type or "").lower().replace(" ", "")
    return "profile=mcp-app" in mime


# ---------------------------------------------------------------------------
# Sizes, types, saving
# ---------------------------------------------------------------------------


def decoded_size(b64: str) -> int:
    """The decoded length of ``b64``, computed without decoding it."""
    length = len(b64)
    padding = 0
    if b64.endswith("=="):
        padding = 2
    elif b64.endswith("="):
        padding = 1
    return max(0, length * 3 // 4 - padding)


def format_size(size: int) -> str:
    if size == 1:
        return "1 byte"
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def is_textual_mime(mime_type: Optional[str]) -> bool:
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if not mime:
        return False
    return (
        mime.startswith("text/")
        or mime in _TEXT_MIME_EXACT
        or mime.endswith(_TEXT_MIME_SUFFIX)
    )


_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/gzip": ".gz",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "video/mp4": ".mp4",
}


def _extension(uri: str, mime_type: Optional[str]) -> str:
    """A short alphanumeric suffix from the URI, else from the MIME type, else none."""
    tail = uri.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    if "." in tail:
        ext = tail.rsplit(".", 1)[1]
        if 1 <= len(ext) <= 8 and ext.isascii() and ext.isalnum():
            return "." + ext.lower()
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    return _MIME_EXTENSIONS.get(mime, "")


#: ``save(data, uri, mime_type) -> Path``. ``None`` where nothing may be saved.
SaveFn = Callable[[bytes, str, Optional[str]], Path]


def save_binary(out_dir: Path, data: bytes, uri: str, mime_type: Optional[str]) -> Path:
    """Write ``data`` to ``out_dir`` as ``mcp-resource_<ts>_<uid><ext>``, mode 0600.

    Then prunes ``out_dir`` with the oversized-result files' pruner: that
    otherwise runs only when a text result spills, and a binary save returns a
    short line that never does — a session that only read binaries would never
    prune.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    name = (
        f"{SAVED_RESOURCE_PREFIX}{int(time.time())}_{uuid.uuid4().hex[:6]}"
        f"{_extension(uri, mime_type)}"
    )
    path = out_dir / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    # Deferred: ``runtime`` sits above ``mcp`` in the import graph.
    from ..runtime.tool_result_formatter import _prune_tool_outputs

    _prune_tool_outputs(out_dir)
    return path


def saver_for(working_directory: Optional[Path]) -> Optional[SaveFn]:
    """The save function for a tool bound to ``working_directory``, or ``None``.

    No working directory means nothing is saved — never a fallback to the
    process cwd, which is not the session's directory under ACP or a host.
    """
    if working_directory is None:
        return None
    # Late import, same reason as in ``save_binary``.
    from ..runtime.tool_result_formatter import _TOOL_OUTPUT_DIR

    out_dir = Path(working_directory) / _TOOL_OUTPUT_DIR

    def save(data: bytes, uri: str, mime_type: Optional[str]) -> Path:
        return save_binary(out_dir, data, uri, mime_type)

    return save


# ---------------------------------------------------------------------------
# Rendering for the model
# ---------------------------------------------------------------------------


def render_blob(uri: str, mime_type: Optional[str], b64: str, save: Optional[SaveFn]) -> str:
    """A blob as text: size-checked first, then decoded as text or saved."""
    mime = mime_type or "application/octet-stream"
    size = decoded_size(b64)
    if size > MAX_BLOB_BYTES:
        return (
            f"[Binary resource {uri} ({mime}, {format_size(size)}) not decoded: "
            f"over the {format_size(MAX_BLOB_BYTES)} limit]"
        )
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return f"Error: the server returned malformed base64 for {uri}"
    if is_textual_mime(mime_type):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return f"Error: the content of {uri} is typed {mime} but is not valid UTF-8"
    if save is None:
        return (
            f"[Binary resource {uri} ({mime}, {format_size(len(data))}) not saved: "
            "this session has no working directory]"
        )
    try:
        path = save(data, uri, mime_type)
    except OSError as e:
        return f"[Binary resource {uri} ({mime}, {format_size(len(data))}) could not be saved: {e}]"
    return f"[Binary resource {uri} ({mime}, {format_size(len(data))}) saved to {path}]"


def render_read(read: ResourceRead, save: Optional[SaveFn]) -> str:
    """``read_mcp_resource``'s answer. Several contents are each labelled with their URI."""
    if not read.contents:
        return f"The server returned no content for {read.uri}"
    parts = []
    for item in read.contents:
        if item.text is not None:
            body = item.text
        elif item.blob is not None:
            body = render_blob(item.uri, item.mime_type, item.blob, save)
        else:
            body = f"[Resource {item.uri} carried neither text nor blob]"
        if len(read.contents) > 1:
            body = f"--- {item.uri} ---\n{body}"
        parts.append(body)
    return "\n\n".join(parts)


def _render_resource_link(block: Any, read_hint: Optional[str]) -> str:
    uri = str(block.uri)
    label = getattr(block, "title", None) or getattr(block, "name", None)
    text = f"[Resource {uri}"
    if label:
        text += f' "{label}"'
    details = []
    mime = field(block, "mimeType", "mime_type")
    if mime:
        details.append(mime)
    size = getattr(block, "size", None)
    if isinstance(size, int):
        details.append(format_size(size))
    if details:
        text += f" ({', '.join(details)})"
    description = getattr(block, "description", None)
    if description:
        text += f": {description}"
    if read_hint is not None:
        text += f'. Read it with read_mcp_resource(server="{read_hint}", uri="{uri}")'
    return text + "]"


def render_call_result(
    result: Any, *, save: Optional[SaveFn] = None, read_hint: Optional[str] = None
) -> str:
    """Render a ``CallToolResult`` for the model.

    ``read_hint`` is the server label to name in a ``resource_link``'s
    ``read_mcp_resource`` hint, or ``None`` to give no hint (the tool is not
    registered, the server's resources are disabled or undeclared).
    """
    content = list(result.content or [])
    many = len(content) > 1
    parts: List[str] = []
    for block in content:
        kind = block.type
        if kind == "text":
            parts.append(block.text)
        elif kind == "image":
            mime = field(block, "mimeType", "mime_type")
            data = getattr(block, "data", None) or ""
            parts.append(f"[image: {mime}, {format_size(decoded_size(data))}]")
        elif kind == "resource":
            resource = block.resource
            uri = str(getattr(resource, "uri", "unknown"))
            text = getattr(resource, "text", None)
            blob = getattr(resource, "blob", None)
            if text is not None:
                parts.append(f"--- {uri} ---\n{text}" if many else text)
            elif blob is not None:
                parts.append(
                    render_blob(uri, field(resource, "mimeType", "mime_type"), blob, save)
                )
            else:
                parts.append(f"[resource: {uri}]")
        elif kind == "resource_link":
            parts.append(_render_resource_link(block, read_hint))
        else:
            parts.append(f"[{kind}]")

    # Fall back to structured output only when there are no content blocks at
    # all. A spec-compliant server returns both ``content`` (for the model) and
    # ``structuredContent``; keep the content then. A server returning *only*
    # ``structuredContent`` would otherwise hand the model an empty string.
    if not content:
        structured = field(result, "structuredContent", "structured_content")
        if structured is not None:
            # ensure_ascii=False keeps CJK/emoji readable; default=str degrades
            # a non-JSON-native value to its repr instead of raising.
            parts.append(json.dumps(structured, ensure_ascii=False, default=str))

    text = "\n".join(parts)
    if field(result, "isError", "is_error"):
        return f"MCP tool error: {text}"
    return text


# ---------------------------------------------------------------------------
# Listings (the tools' JSON and the all-servers walk)
# ---------------------------------------------------------------------------


def _drop_none(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def resource_item(info: ResourceInfo) -> Dict[str, Any]:
    return _drop_none({
        "server": info.server,
        "uri": info.uri,
        "name": info.name,
        "title": info.title,
        "description": info.description,
        "mimeType": info.mime_type,
        "size": info.size,
    })


def template_item(info: ResourceTemplateInfo) -> Dict[str, Any]:
    return _drop_none({
        "server": info.server,
        "uriTemplate": info.uri_template,
        "name": info.name,
        "title": info.title,
        "description": info.description,
        "mimeType": info.mime_type,
    })


def visible_resources(items: List[ResourceInfo]) -> List[ResourceInfo]:
    return [i for i in items if not is_mcp_app(i.uri, i.mime_type)]


def visible_templates(items: List[ResourceTemplateInfo]) -> List[ResourceTemplateInfo]:
    return [i for i in items if not is_mcp_app(i.uri_template, i.mime_type)]


def walk_all_pages(
    server: str, fetch: Callable[[Optional[str]], Tuple[List[Any], Optional[str]]], what: str
) -> List[Any]:
    """Every page of one server's listing, under ``tools/list``'s four bounds."""
    items: List[Any] = []
    seen: set = set()
    cursor: Optional[str] = None
    for _ in range(MAX_LIST_PAGES):
        page, cursor = fetch(cursor)
        if len(items) + len(page) > MAX_LIST_ITEMS:
            raise McpResourceError(
                server, "catalog",
                f"MCP server '{server}' listed more than {MAX_LIST_ITEMS} {what}",
            )
        items.extend(page)
        if cursor is None:
            return items
        # The manager already refused a cursor over MAX_CURSOR_BYTES.
        if cursor in seen:
            raise McpResourceError(
                server, "catalog",
                f"MCP server '{server}' returned a repeated {what} pagination cursor",
            )
        seen.add(cursor)
    raise McpResourceError(
        server, "catalog", f"MCP server '{server}' exceeded {MAX_LIST_PAGES} pages of {what}"
    )


def list_everywhere(
    manager: Any, *, templates: bool
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """Every page of every listed server, sorted by label; failures in ``errors``.

    A server whose connection turns out to declare no ``resources`` is omitted
    rather than reported.
    """
    collected: List[Any] = []
    errors: List[Dict[str, str]] = []
    what = "resource templates" if templates else "resources"
    for server in manager.resource_servers():
        def fetch(cursor: Optional[str], _server: str = server):
            if templates:
                page = manager.list_resource_templates(_server, cursor)
                return page.templates, page.next_cursor
            page = manager.list_resources(_server, cursor)
            return page.resources, page.next_cursor

        try:
            collected.extend(walk_all_pages(server, fetch, what))
        except McpResourceError as e:
            if e.kind == "unsupported":
                continue
            errors.append({"server": server, "error": str(e)})
    return collected, errors
