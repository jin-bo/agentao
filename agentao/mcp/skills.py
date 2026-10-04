"""MCP Skills extension (``io.modelcontextprotocol/skills``), client side.

docs/design/mcp-skills.md. Three layers live here:

- the **wire** — agentao's own ``skills/list`` / ``skills/get`` request models
  over ``ClientSession.send_request`` (D10), and entry validation (§5.3);
- **verification** — size, digest and frontmatter checks on every byte that
  is used (§5.5 step 2, §6.1);
- :class:`McpSkills` — one per session, shared with every sub-agent spawned
  from it: the catalogue, the held entries, the approvals, the cache, and the
  gates the planner asks about (§5.5 step 3, §6).

The manager it talks to is duck-typed (``get_skill`` / ``read_skill_resource``
/ ``skill_listing``), so nothing here imports the client.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import posixpath
import re
import threading
import unicodedata
import weakref
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from ..security.unicode_tags import strip_unicode_tags
from ..tools.base import SHELL_TOOL_NAME
from .resources import (
    McpResourceError,
    ResourceContent,
    ResourceRead,
    format_size,
    is_textual_mime,
)

logger = logging.getLogger("agentao.mcp.skills")

#: The extension identifier a server declares under ``capabilities.extensions``.
EXTENSION_ID = "io.modelcontextprotocol/skills"

#: The base revision the extension is specified against. Versions are ISO
#: dates, so a string comparison orders them.
MIN_PROTOCOL_VERSION = "2026-07-28"

#: Prefix of every model-facing MCP skill name, reserved for them (§5.3).
NAME_PREFIX = "mcp:"

#: The spec's per-skill limits (hosts MUST support up to these).
MAX_SKILL_FILES = 512
MAX_SKILL_BYTES = 16 * 1024 * 1024

#: Our own limit on SKILL.md alone. An active skill's SKILL.md rides every
#: request (the volatile tail), outside history, so no compaction can shrink
#: it: one over the model's window would fail every turn until deactivated.
#: Bytes, not characters, because the manifest's size is bytes and
#: verification holds the content to it exactly — so the listing check is
#: the only one needed. Long material belongs in a supporting file, which
#: ``read_skill_file`` pages.
MAX_SKILL_MD_BYTES = 100_000

#: Listing bounds: ``tools/list``'s, with the skill count per server (§5.3).
MAX_SKILL_PAGES = 100
MAX_SKILLS = 1024
MAX_CURSOR_BYTES = 64 * 1024

#: Catalogue text from a server is cut to this (the Agent Skills limit).
MAX_DESCRIPTION_CHARS = 1024

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SKILL_MD = "/SKILL.md"
# C0 / C1 control characters except tab and newline.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

#: Tool names the gates read. Literals rather than imports: ``tools`` must not
#: import ``mcp`` (the resource tools' cycle), and these are wire names.
ACTIVATE_SKILL_TOOL = "activate_skill"
READ_RESOURCE_TOOL = "read_mcp_resource"
READ_SKILL_FILE_TOOL = "read_skill_file"

#: The reason family on a decision the gates tightened, so a host can tell it
#: from an engine rule.
GATE_REASON = "mcp-skill"


# ---------------------------------------------------------------------------
# Wire
# ---------------------------------------------------------------------------


def _request_models():
    """``(ListSkillsRequest, GetSkillRequest, GetSkillParams, result adapter)`` on mcp 2.x.

    Built on first use: only an SDK with the modern era reaches a Skills
    request (§5.2), and these subclass 2.x's request bases.
    """
    global _MODELS
    if _MODELS is None:
        from typing import Literal

        from mcp.types import PaginatedRequest, Request, RequestParams
        from pydantic import TypeAdapter

        class ListSkillsRequest(PaginatedRequest[Literal["skills/list"]]):
            method: Literal["skills/list"] = "skills/list"

        class GetSkillParams(RequestParams):
            uri: str

        class GetSkillRequest(Request[GetSkillParams, Literal["skills/get"]]):
            method: Literal["skills/get"] = "skills/get"
            params: GetSkillParams

        _MODELS = (
            ListSkillsRequest, GetSkillRequest, GetSkillParams, TypeAdapter(Dict[str, Any])
        )
    return _MODELS


_MODELS = None


def list_skills_request(cursor: Optional[str]) -> Any:
    from mcp.types import PaginatedRequestParams

    list_request = _request_models()[0]
    params = PaginatedRequestParams(cursor=cursor) if cursor is not None else None
    return list_request(params=params)


def get_skill_request(uri: str) -> Any:
    _list, get_request, get_params, _adapter = _request_models()
    return get_request(params=get_params(uri=uri))


def result_adapter() -> Any:
    return _request_models()[3]


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillFile:
    uri: str
    digest: str
    size: int


@dataclass(frozen=True)
class SkillEntry:
    """One validated ``Skill`` entry from server ``label``.

    ``files`` is ``None`` for a ``"dynamic"`` skill. ``unavailable`` is the
    reason the skill cannot be loaded (dynamic, over a limit), or ``None``.
    """

    label: str
    uri: str
    frontmatter: Dict[str, Any]
    files: Optional[Tuple[SkillFile, ...]]
    unavailable: Optional[str] = None

    @property
    def key(self) -> Tuple[str, str]:
        return (self.label, self.uri)

    @property
    def name(self) -> str:
        return str(self.frontmatter.get("name", ""))

    @property
    def description(self) -> str:
        return str(self.frontmatter.get("description", ""))

    @property
    def model_name(self) -> str:
        return model_name(self.label, self.uri)

    @property
    def root(self) -> str:
        return skill_root(self.uri)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files or ())

    def manifest(self) -> Tuple[Tuple[str, str], ...]:
        """What an approval binds to: every ``(uri, digest)``, sorted."""
        return tuple(sorted((f.uri, f.digest) for f in self.files or ()))

    def file(self, uri: str) -> Optional[SkillFile]:
        for f in self.files or ():
            if f.uri == uri:
                return f
        return None


class SkillEntryError(ValueError):
    """An entry that fails §5.3's validation; the message is the reason."""


def model_name(label: str, uri: str) -> str:
    """``mcp:<label>:<uri>`` — the identity, spelled out (§5.3)."""
    return f"{NAME_PREFIX}{label}:{uri}"


def parse_model_name(name: str, labels: Any) -> Optional[Tuple[str, str]]:
    """``(label, uri)`` for a model-facing name, or ``None``.

    Matched against the known ``labels`` (longest first), not split on the
    first ``:``: a label may itself contain one.
    """
    if not isinstance(name, str) or not name.startswith(NAME_PREFIX):
        return None
    rest = name[len(NAME_PREFIX):]
    for label in sorted(labels, key=len, reverse=True):
        uri = rest[len(label) + 1:]
        # Only a split that leaves a skill URI: with labels ``docs`` and
        # ``docs:skill``, ``mcp:docs:skill://x/SKILL.md`` is ``docs``'s.
        if rest.startswith(label + ":") and "://" in uri and uri.endswith(_SKILL_MD):
            return label, uri
    return None


def skill_root(uri: str) -> str:
    """The skill directory: the SKILL.md URI minus ``/SKILL.md``."""
    return uri[: -len(_SKILL_MD)] if uri.endswith(_SKILL_MD) else uri


def _final_segment(uri: str) -> str:
    return skill_root(uri).rstrip("/").rsplit("/", 1)[-1]


def validate_entry(label: str, raw: Any) -> SkillEntry:
    """Validate one wire entry (§5.3). Raises :class:`SkillEntryError`.

    A ``"dynamic"`` skill or one over a limit is *valid* and returned with
    ``unavailable`` set, so the user can be told why it cannot be loaded.
    """
    if not isinstance(raw, dict):
        raise SkillEntryError("entry is not an object")
    uri = raw.get("uri")
    if not isinstance(uri, str) or not uri.endswith(_SKILL_MD) or uri == _SKILL_MD.lstrip("/"):
        raise SkillEntryError(f"uri {uri!r} does not end in /SKILL.md")
    if not _is_clean(uri):
        raise SkillEntryError(f"uri {uri!r} contains control, whitespace or invisible characters")
    if not _is_canonical(uri):
        # Every comparison against a held skill's root (same-origin reads,
        # manifest lookups) is by string: an entry spelled with ``.`` / ``..``
        # or an empty segment would compare unequal to its own files.
        raise SkillEntryError(f"uri {uri!r} is not canonical (empty, '.' or '..' segment)")
    frontmatter = raw.get("frontmatter")
    if not isinstance(frontmatter, dict):
        raise SkillEntryError("frontmatter is not an object")
    name = frontmatter.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not _NAME_RE.match(name):
        raise SkillEntryError(f"name {name!r} breaks the Agent Skills naming rules")
    if _final_segment(uri) != name:
        raise SkillEntryError(f"name {name!r} is not the final path segment of {uri}")
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillEntryError("description is missing or empty")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillEntryError(f"description is longer than {MAX_DESCRIPTION_CHARS} characters")

    resources = raw.get("resources")
    if resources == "dynamic":
        return SkillEntry(
            label, uri, dict(frontmatter), None,
            unavailable="its content is generated (\"dynamic\"), so it cannot be verified",
        )
    if not isinstance(resources, list):
        raise SkillEntryError("resources is neither an array nor \"dynamic\"")
    root = skill_root(uri) + "/"
    files: List[SkillFile] = []
    seen: set = set()
    for item in resources:
        if not isinstance(item, dict):
            raise SkillEntryError("a resources item is not an object")
        f_uri, digest, size = item.get("uri"), item.get("digest"), item.get("size")
        if not isinstance(f_uri, str) or not (f_uri == uri or f_uri.startswith(root)):
            raise SkillEntryError(f"resource {f_uri!r} is outside the skill directory")
        if not _is_clean(f_uri):
            # File paths are listed in the active-skills prompt every turn.
            raise SkillEntryError(
                f"resource {f_uri!r} contains control, whitespace or invisible characters"
            )
        # Canonical too: the root is (checked above), and ``_escapes`` refuses an
        # empty, "." or ".." segment below it.
        if _escapes(f_uri[len(root):] if f_uri != uri else "SKILL.md"):
            raise SkillEntryError(f"resource {f_uri!r} is outside the skill directory")
        if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
            raise SkillEntryError(f"resource {f_uri} has a malformed digest")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise SkillEntryError(f"resource {f_uri} has a malformed size")
        if f_uri in seen:
            raise SkillEntryError(f"resource {f_uri} is listed twice")
        seen.add(f_uri)
        files.append(SkillFile(f_uri, digest, size))
    if uri not in seen:
        raise SkillEntryError("resources has no entry for SKILL.md itself")
    entry = SkillEntry(label, uri, dict(frontmatter), tuple(files))
    if len(files) > MAX_SKILL_FILES:
        return _unavailable(entry, f"it has {len(files)} files (limit {MAX_SKILL_FILES})")
    if entry.total_size > MAX_SKILL_BYTES:
        return _unavailable(
            entry, f"it is {format_size(entry.total_size)} (limit {format_size(MAX_SKILL_BYTES)})"
        )
    skill_md_size = entry.file(uri).size
    if skill_md_size > MAX_SKILL_MD_BYTES:
        return _unavailable(
            entry,
            f"its SKILL.md is {format_size(skill_md_size)} (limit "
            f"{format_size(MAX_SKILL_MD_BYTES)}; it is sent with every request "
            "while active)",
        )
    return entry


def _unavailable(entry: SkillEntry, reason: str) -> SkillEntry:
    return SkillEntry(entry.label, entry.uri, entry.frontmatter, entry.files, unavailable=reason)


def _is_clean(uri: str) -> bool:
    """No Unicode tag characters, control characters or whitespace.

    URIs reach the system prompt (the catalogue, the active-skills file
    list) verbatim, so a hidden character there would smuggle server text
    past the tag stripping applied to descriptions and bodies.
    """
    return (
        strip_unicode_tags(uri) == uri
        and not _CONTROL_RE.search(uri)
        and not any(ch.isspace() for ch in uri)
        and not any(unicodedata.category(ch) == "Cf" for ch in uri)
    )


def _is_canonical(uri: str) -> bool:
    """``scheme://authority/path`` with no empty, ``.`` or ``..`` path segment.

    The authority may be empty — ``file:///skills/refunds/SKILL.md`` is a
    valid absolute file URI, and the extension privileges no scheme.
    """
    scheme, sep, rest = uri.partition("://")
    if not sep or not scheme:
        return False
    authority, slash, path = rest.partition("/")
    if authority in (".", "..") or not slash or not path:
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def _escapes(relative: str) -> bool:
    """Whether a path relative to the skill root leaves it."""
    if not relative or relative.startswith("/"):
        return True
    return any(part in ("", ".", "..") for part in relative.split("/"))


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class SkillVerificationError(RuntimeError):
    """Bytes that do not match the held entry. Never use them."""


def content_bytes(read: ResourceRead, expected: SkillFile) -> Tuple[bytes, Optional[str]]:
    """The raw bytes of a single-file read, and its MIME type.

    Text is the UTF-8 encoding of ``text`` (the spec's example digests are
    over exactly that); a blob is base64-decoded — after its encoded length
    is checked against ``size``, so a hostile blob is never decoded whole.
    """
    contents = [c for c in read.contents if c.uri == expected.uri] or read.contents
    if len(contents) != 1:
        raise SkillVerificationError(
            f"{expected.uri}: expected one content item, got {len(contents)}"
        )
    item = contents[0]
    if item.text is not None:
        data = item.text.encode("utf-8")
    elif item.blob is not None:
        if len(item.blob) > (expected.size + 2) // 3 * 4 + 4:
            raise SkillVerificationError(
                f"{expected.uri}: content is larger than the {expected.size} bytes listed"
            )
        try:
            data = base64.b64decode(item.blob, validate=True)
        except (binascii.Error, ValueError):
            raise SkillVerificationError(f"{expected.uri}: blob is not valid base64") from None
    else:
        raise SkillVerificationError(f"{expected.uri}: the read returned no content")
    return data, item.mime_type


def verify(data: bytes, expected: SkillFile) -> None:
    """Size first (cheap, and a mismatch on its own is a failure), then digest."""
    if len(data) != expected.size:
        raise SkillVerificationError(
            f"{expected.uri}: {len(data)} bytes, the entry lists {expected.size}"
        )
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if digest != expected.digest:
        raise SkillVerificationError(f"{expected.uri}: digest does not match the entry")


def parse_skill_frontmatter(text: str) -> Optional[Dict[str, Any]]:
    """The SKILL.md frontmatter as JSON-comparable data, or ``None``.

    Strict, unlike :func:`agentao.frontmatter.parse_frontmatter`: a missing
    fence, malformed YAML or a non-mapping is a failure here, not ``{}``.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            try:
                meta = yaml.safe_load("".join(lines[1:i]))
            except yaml.YAMLError:
                return None
            if not isinstance(meta, dict):
                return None
            # YAML dates and the like become strings, as in the JSON entry.
            return json.loads(json.dumps(meta, default=str))
    return None


def check_frontmatter(text: str, entry: SkillEntry) -> None:
    parsed = parse_skill_frontmatter(text)
    if parsed is None:
        raise SkillVerificationError(f"{entry.uri}: SKILL.md has no readable frontmatter")
    expected = json.loads(json.dumps(entry.frontmatter, default=str))
    if parsed != expected:
        fields = sorted(
            k for k in set(parsed) | set(expected) if parsed.get(k) != expected.get(k)
        )
        raise SkillVerificationError(
            f"{entry.uri}: SKILL.md frontmatter differs from the entry "
            f"({', '.join(fields)})"
        )


def sanitize_text(text: str, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """Server-written text bound for the system prompt (§5.4)."""
    cleaned = _CONTROL_RE.sub(" ", strip_unicode_tags(text or ""))
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

#: An approval given for a load by URI, before the entry was known: the
#: entry ``skills/get`` returns at activation is the one approved.
_BY_URI = object()
_FINGERPRINT_RE = re.compile(r"Manifest ([0-9a-f]{16})\.\Z")


def manifest_fingerprint(key: Tuple[str, str], manifest: Any) -> str:
    """A short, stable name for one skill's manifest, shown in its prompt."""
    blob = json.dumps([list(key), [list(pair) for pair in manifest]], separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class HeldSkill:
    """A loaded skill: the entry it was verified against, and its SKILL.md."""

    entry: SkillEntry
    text: str
    #: The conversation generation that loaded it (see :class:`McpSkills`).
    generation: int = 0


@dataclass
class _ServerListing:
    entries: List[SkillEntry] = dc_field(default_factory=list)
    unavailable: List[Tuple[str, str]] = dc_field(default_factory=list)  # (uri, reason)
    problem: Optional[str] = None


class McpSkills:
    """MCP skills for one session, shared with every sub-agent spawned from it.

    Holds, under one lock:

    - the **catalogue**, fixed at construction (D8);
    - ``_entries`` — the current entry per ``(label, uri)``: the listed one,
      one loaded by URI (D2), or one a refresh replaced;
    - ``_held`` — the held-entry map (§5.5 step 3). Its keys' labels are the
      origins every §6 gate reads;
    - ``_approved`` — ``(label, uri) → manifest`` the user approved (D5: for
      the session only);

    Both are keyed by a **conversation generation** as well. ``/clear`` starts
    a new one (:meth:`clear_held`) instead of emptying them, because a
    background sub-agent may still be running on the old conversation, with
    the old skills' instructions in its context: its gates must stay on while
    the parent's lift. Each reader passes its *view* — ``None`` for the
    session's current generation (the parent), or the generation a sub-agent
    was spawned in (``SkillManager.mcp_view``).
    - ``_cache`` — verified bytes keyed ``(label, uri, digest)`` (D6).
    """

    def __init__(self, manager: Any, servers: List[str]):
        self._manager = manager
        #: Servers that passed the §5.2 gate, sorted.
        self.servers: List[str] = sorted(servers)
        self._lock = threading.RLock()
        self._listings: Dict[str, _ServerListing] = {}
        self._entries: Dict[Tuple[str, str], SkillEntry] = {}
        self.generation = 0
        self._held: Dict[Tuple[int, Tuple[str, str]], HeldSkill] = {}
        self._approved: Dict[Tuple[int, Tuple[str, str]], Any] = {}
        # Manifests a consent prompt offered, by the fingerprint the prompt
        # shows. Content-addressed, so concurrent prompts for one skill —
        # foreground sub-agents, a refresh between two — never overwrite each
        # other: :meth:`confirmed` approves the manifest named by the very
        # prompt the user answered (§5.5 step 1).
        self._offered: Dict[str, Any] = {}
        self._cache: Dict[Tuple[str, str, str], Tuple[bytes, Optional[str]]] = {}
        # Origins of MCP skill activations in batches still running, keyed
        # by a per-batch token (sub-agents run batches concurrently).
        self._pending: Dict[object, Tuple[int, List[str]]] = {}
        # Sub-agents' skill managers, weakly: a retired generation's state is
        # kept only while a live one still reads it (:meth:`_prune`).
        self._views: "weakref.WeakSet[Any]" = weakref.WeakSet()
        # Per generation: origins of MCP skill content the conversation
        # carries without a held entry — a restored compaction summary.
        self._tainted: Dict[int, set] = {}
        for label in self.servers:
            entries, unavailable, problem = manager.skill_listing(label)
            listing = _ServerListing(list(entries), list(unavailable), problem)
            self._listings[label] = listing
            for entry in listing.entries:
                self._entries[entry.key] = entry

    # -- catalogue -------------------------------------------------------

    def catalogue(self) -> List[SkillEntry]:
        """Every listed entry, loadable or not, in a stable order."""
        out = [e for label in self.servers for e in self._listings[label].entries]
        return sorted(out, key=lambda e: (e.label, e.uri))

    def listing(self, label: str) -> Optional[_ServerListing]:
        return self._listings.get(label)

    def entry(self, key: Tuple[str, str]) -> Optional[SkillEntry]:
        with self._lock:
            return self._entries.get(key)

    def resolve_name(self, name: str) -> Optional[Tuple[str, str]]:
        return parse_model_name(name, self.servers)

    # -- held entries and gates --------------------------------------------

    def _gen(self, view: Optional[int]) -> int:
        return self.generation if view is None else view

    def held(self, key: Tuple[str, str], view: Optional[int] = None) -> Optional[HeldSkill]:
        with self._lock:
            return self._held.get((self._gen(view), key))

    def held_skills(self, view: Optional[int] = None) -> List[HeldSkill]:
        with self._lock:
            gen = self._gen(view)
            return [h for (g, _), h in self._held.items() if g == gen]

    def origins(self, view: Optional[int] = None) -> List[str]:
        """Origins of every loaded skill, and of activations in running batches."""
        with self._lock:
            gen = self._gen(view)
            labels = {label for (g, (label, _)) in self._held if g == gen}
            labels.update(self._tainted.get(gen, ()))
            for pending_gen, pending in self._pending.values():
                if pending_gen == gen:
                    labels.update(pending)
            return sorted(labels)

    def taint(self, labels: Any, view: Optional[int] = None) -> None:
        """Count ``labels`` as loaded in this generation, with no held entry.

        For skill content a conversation carries that no load in it backs —
        a summary restored from another session. Gates only: nothing is
        approved, and no file becomes readable. ``*`` (an unknown server)
        gates every resource read.
        """
        with self._lock:
            self._tainted.setdefault(self._gen(view), set()).update(labels)

    def register_view(self, manager: Any) -> None:
        """Note a sub-agent's skill manager reading ``manager.mcp_view``."""
        with self._lock:
            self._views.add(manager)
            self._prune()

    def _prune(self) -> None:
        """Drop retired generations no live sub-agent reads. Caller holds the lock.

        Their held SKILL.md text, approvals and taints would otherwise
        accumulate across every ``/clear`` for the life of the process.
        Cached bytes no remaining held entry lists go with them.
        """
        live = {self.generation}
        for manager in list(self._views):
            view = getattr(manager, "mcp_view", None)
            if isinstance(view, int):
                live.add(view)
        for store in (self._held, self._approved):
            for key in [k for k in store if k[0] not in live]:
                del store[key]
        for gen in [g for g in self._tainted if g not in live]:
            del self._tainted[gen]
        listed = {
            (held.entry.label, f.uri, f.digest)
            for held in self._held.values() for f in held.entry.files or ()
        }
        for key in [k for k in self._cache if k not in listed]:
            del self._cache[key]

    def begin_batch(self, arguments: Any, view: Optional[int] = None) -> Optional[object]:
        """Count a batch's MCP skill activations as loaded until it ends.

        The batch is planned and confirmed as a whole and then run in
        parallel, so an activation in it can complete while another call of
        the same batch runs. Reading the activation's origin now gates those
        calls as if it had already loaded — conservatively, even if the user
        then refuses it. ``arguments`` is each call's parsed arguments (the
        runner parses them as the planner does); any call whose
        ``skill_name`` names an MCP skill counts, whatever the tool name says.
        """
        labels = set()
        for args in arguments or ():
            name = args.get("skill_name") if isinstance(args, dict) else None
            key = self.resolve_name(name) if isinstance(name, str) else None
            if key is not None:
                labels.add(key[0])
        if not labels:
            return None
        token = object()
        with self._lock:
            self._pending[token] = (self._gen(view), sorted(labels))
        return token

    def end_batch(self, token: Optional[object]) -> None:
        if token is None:
            return
        with self._lock:
            self._pending.pop(token, None)

    def clear_held(self) -> None:
        """``/clear`` / a new session: start a new conversation generation.

        For the current view the acting windows end, the gates lift, and the
        approvals go — the next conversation asks again (D5). Entries of the
        old generation stay for any sub-agent still running on it.
        """
        with self._lock:
            self.generation += 1
            self._prune()

    def forget_server(self, label: str) -> None:
        """Drop a server's approvals and cached files (logout, ``/mcp remove``).

        Every generation's: a re-login is a new trust decision about the
        server, for whichever conversation asks next.
        """
        with self._lock:
            for key in [k for k in self._approved if k[1][0] == label]:
                del self._approved[key]
            self._offered.clear()  # cheap to rebuild: the next prompt re-offers
            for key in [k for k in self._cache if k[0] == label]:
                del self._cache[key]

    def is_approved(self, entry: SkillEntry, view: Optional[int] = None) -> bool:
        with self._lock:
            return self._approved.get((self._gen(view), entry.key)) == entry.manifest()

    def gate(
        self, tool_name: str, args: Dict[str, Any], tool: Any, view: Optional[int] = None,
    ) -> Optional[str]:
        """The prompt text when this call must be asked, or ``None`` (§5.5, §6).

        Consulted by the planner *after* every DENY path, so it only ever
        tightens ALLOW / no-match to ASK.
        """
        if tool_name == ACTIVATE_SKILL_TOOL:
            return self._consent_note(args.get("skill_name"), view)
        origins = self.origins(view)
        if not origins:
            return None
        loaded = ", ".join(
            h.entry.model_name for h in sorted(self.held_skills(view), key=lambda h: h.entry.key)
        )
        if not loaded:
            # No held entry: the origins are a pending activation in a running
            # batch, or content carried in with no load behind it (a restored
            # transcript, a sub-agent's marked result — :meth:`taint`).
            with self._lock:
                gen = self._gen(view)
                pending = any(g == gen for g, _labels in self._pending.values())
            loaded = (
                "one is being activated in this batch, from "
                if pending
                else "content carried into this conversation, from "
            ) + ", ".join(origins)
        if tool_name == SHELL_TOOL_NAME:
            return (
                f"An MCP skill is loaded in this session ({loaded}). Its instructions "
                "come from an MCP server, so every command is asked."
            )
        if getattr(tool, "spawns_shell_capable_agent", False) is True:
            return (
                f"An MCP skill is loaded in this session ({loaded}). This sub-agent "
                "can run shell commands, so spawning it is asked."
            )
        if tool_name == READ_RESOURCE_TOOL:
            server = args.get("server")
            if origins != [server]:
                others = ", ".join(o for o in origins if o != server) or ", ".join(origins)
                return (
                    f"Cross-origin read: this reads from MCP server '{server}' while "
                    f"skills from {others} are loaded ({loaded})."
                )
        return None

    def _consent_note(self, name: Any, view: Optional[int] = None) -> Optional[str]:
        key = self.resolve_name(name) if isinstance(name, str) else None
        if key is None:
            return None
        entry = self.entry(key)
        gen = self._gen(view)
        if entry is None:
            fingerprint = manifest_fingerprint(key, (("by-uri", ""),))
            with self._lock:
                self._offered[fingerprint] = (key, _BY_URI)
            return (
                f"Load an MCP skill by URI from server '{key[0]}': {key[1]}. "
                "It is not in the server's listing; its content will be verified "
                f"against the server's entry before use. Manifest {fingerprint}."
            )
        if entry.unavailable or self.is_approved(entry, view):
            return None
        manifest = entry.manifest()
        fingerprint = manifest_fingerprint(entry.key, manifest)
        with self._lock:
            changed = (gen, entry.key) in self._approved
            self._offered[fingerprint] = (entry.key, manifest)
        head = "Changed — re-approve. " if changed else ""
        return (
            f"{head}Load MCP skill '{entry.name}' from server '{entry.label}' "
            f"({entry.uri}): {sanitize_text(entry.description, 300)} — "
            f"{len(entry.files or ())} file(s), {format_size(entry.total_size)}. "
            "Its instructions come from that server, not from you or this project. "
            f"Manifest {fingerprint}."
        )

    def confirmed(
        self, tool_name: str, args: Dict[str, Any], view: Optional[int] = None,
        note: Optional[str] = None,
    ) -> None:
        """The user approved a gated call: approve what *that* prompt offered.

        ``note`` is the prompt text the user answered. Its manifest
        fingerprint names the manifest approved — not whatever the skill's
        entry holds now, which a refresh or a concurrent prompt may have
        changed since.
        """
        if tool_name != ACTIVATE_SKILL_TOOL or not isinstance(note, str):
            return
        name = args.get("skill_name") if isinstance(args, dict) else None
        key = self.resolve_name(name) if isinstance(name, str) else None
        if key is None:
            return
        gen = self._gen(view)
        # Only the fingerprint the note *ends* with — the one this module
        # appended. A server-written description inside the note may contain
        # text shaped like one; it is never metadata.
        match = _FINGERPRINT_RE.search(note)
        with self._lock:
            offered = self._offered.get(match.group(1)) if match else None
            if offered is not None and offered[0] == key:
                self._approved[(gen, key)] = offered[1]

    # -- activation ------------------------------------------------------

    def activate(
        self, name: str, view: Optional[int] = None, *, require_approval: bool = False,
    ) -> Tuple[Optional[HeldSkill], str]:
        """Load ``name``: fetch, verify, hold (§5.5 steps 2-3).

        Returns ``(held, "")`` or ``(None, error)``. With
        ``require_approval`` — the model's ``activate_skill`` — the entry's
        current manifest must be the one the user approved (:meth:`confirmed`):
        a skill that changed after the prompt, even within one batch, is
        refused rather than loaded under consent given for other content.
        Without it — a user's own ``/skills activate`` — the call is the
        consent, and the current manifest is recorded as approved.
        """
        key = self.resolve_name(name)
        if key is None:
            return None, f"Error: '{name}' does not name a skill on a Skills-enabled MCP server."
        entry = self.entry(key)
        if entry is None:
            try:
                entry = self._manager.get_skill(*key)
            except McpResourceError as e:
                return None, f"Error: {e}"
            with self._lock:
                self._entries[key] = entry
        if entry.unavailable:
            return None, f"Error: MCP skill '{name}' cannot be loaded: {entry.unavailable}."
        gen = self._gen(view)
        with self._lock:
            approved = self._approved.get((gen, key))
            if not require_approval or approved is _BY_URI:
                self._approved[(gen, key)] = entry.manifest()
            elif approved != entry.manifest():
                return None, (
                    f"Error: MCP skill {name} is not approved in its current version "
                    "(it changed after the user was asked, or was never approved). "
                    "Call activate_skill again to ask the user."
                )
        skill_md = entry.file(entry.uri)
        assert skill_md is not None  # validate_entry requires it
        try:
            data, _mime = self._fetch(entry, skill_md)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise SkillVerificationError(f"{entry.uri}: SKILL.md is not UTF-8") from None
            check_frontmatter(text, entry)
        except SkillVerificationError as e:
            return None, self._after_failure(entry, str(e), gen)
        except McpResourceError as e:
            return None, f"Error: {e}"
        held = HeldSkill(entry, text, gen)
        with self._lock:
            self._held[(gen, key)] = held
        return held, ""

    def _after_failure(self, entry: SkillEntry, problem: str, gen: int) -> str:
        """§5.5 step 2: one ``skills/get`` decides what the failure means."""
        logger.warning("MCP skill verification failed: %s", problem)
        try:
            fresh = self._manager.get_skill(entry.label, entry.uri)
        except McpResourceError as e:
            return f"Error: MCP skill verification failed ({problem}); refreshing it failed: {e}"
        if fresh.manifest() == entry.manifest() and fresh.frontmatter == entry.frontmatter:
            return (
                f"Error: MCP server '{entry.label}' served content that does not match "
                f"its own entry for {entry.uri} ({problem}). The skill was not used."
            )
        with self._lock:
            self._entries[entry.key] = fresh
            # Revoked: the approval bound a set that no longer exists. Kept as
            # a marker so the next prompt reads "changed — re-approve".
            self._approved[(gen, entry.key)] = ()
        return (
            f"Error: MCP skill {entry.model_name} changed on server '{entry.label}' "
            "since it was approved, so the approval was revoked. Call activate_skill "
            "again to show the user the new version for approval."
        )

    def _fetch(self, entry: SkillEntry, expected: SkillFile) -> Tuple[bytes, Optional[str]]:
        """Verified bytes of one manifest file, through the cache (§6.3)."""
        cache_key = (entry.label, expected.uri, expected.digest)
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            # The key carries the digest; the size is checked again against
            # the *current* entry, which a refresh may have changed while
            # keeping the digest. The MIME type comes back with the bytes, so
            # a repeated read is classified (text / binary) as the first was.
            verify(cached[0], expected)
            return cached
        read = self._manager.read_skill_resource(entry.label, expected.uri)
        data, mime = content_bytes(read, expected)
        verify(data, expected)
        with self._lock:
            self._cache[cache_key] = (data, mime)
        return data, mime

    # -- reading files ---------------------------------------------------

    def resolve_file(self, held: HeldSkill, path: str) -> Tuple[Optional[SkillFile], str]:
        """The manifest file ``path`` names in a held skill, or an error."""
        entry = held.entry
        root = entry.root
        if path.startswith(root + "/"):
            relative = path[len(root) + 1:]
        else:
            relative = path
            while relative.startswith("./"):
                relative = relative[2:]
        normalised = posixpath.normpath(relative) if relative else ""
        if not normalised or normalised.startswith(("../", "/")) or normalised in (".", ".."):
            return None, f"Error: '{path}' is not a path inside the skill {entry.model_name}."
        uri = f"{root}/{normalised}"
        found = entry.file(uri)
        if found is None:
            return None, (
                f"Error: '{normalised}' is not in the manifest of {entry.model_name}, "
                "so it is not read. Its files are: "
                + ", ".join(relative_path(entry, f.uri) for f in entry.files or ())
            )
        return found, ""

    def read_file(self, held: HeldSkill, expected: SkillFile) -> Tuple[Optional[bytes], Optional[str], str]:
        """``(bytes, mime, "")`` verified, or ``(None, None, error)``."""
        try:
            data, mime = self._fetch(held.entry, expected)
        except SkillVerificationError as e:
            return None, None, self._after_failure(held.entry, str(e), held.generation)
        except McpResourceError as e:
            return None, None, f"Error: {e}"
        return data, mime, ""

    def generic_read(
        self, server: str, uri: str, view: Optional[int] = None,
    ) -> Optional[Callable[[], ResourceRead]]:
        """§6.1: a ``read_mcp_resource`` inside a skill loaded from ``server``.

        ``None`` when the read is an ordinary one. Otherwise a callable that
        returns the verified read, or raises :class:`McpResourceError` — for
        an unlisted file before any request.
        """
        # Most specific root first: with ``parent`` and ``parent/child`` both
        # loaded, a file of the child is the child's, in either load order.
        for held in sorted(self.held_skills(view), key=lambda h: -len(h.entry.root)):
            entry = held.entry
            if entry.label != server:
                continue
            target = _normalise_uri(uri)
            if target != entry.uri and not target.startswith(entry.root + "/"):
                continue
            expected = entry.file(target)

            def run(held=held, expected=expected, target=target) -> ResourceRead:
                if expected is None:
                    raise McpResourceError(
                        server, "not_found",
                        f"{target} is not in the manifest of the loaded MCP skill "
                        f"{held.entry.model_name}, so it is not read — use "
                        f"read_skill_file(skill=\"{held.entry.model_name}\", path=...) "
                        "with a listed path.",
                    )
                data, mime, error = self.read_file(held, expected)
                if data is None:
                    raise McpResourceError(server, "error", error.removeprefix("Error: "))
                return _as_read(server, target, data, mime)

            return run
        return None


def wrap_skill_file(server: str, uri: str, text: str) -> str:
    """A skill file's content, marked as such for the model and for a restore.

    The marker is what ``withhold_mcp_skill_content`` finds when a session is
    restored: content of a loaded skill — through ``read_skill_file`` or a
    verified ``read_mcp_resource`` — must not reach a new session ungated.
    """
    from html import escape

    body = text.replace("</mcp-skill-file", "<\\/mcp-skill-file")
    return (
        f'<mcp-skill-file server="{escape(server)}" uri="{escape(uri)}">\n'
        f"{body}\n</mcp-skill-file>"
    )


def restored_gate_note(tool_name: str, tool: Any, origins: List[str]) -> Optional[str]:
    """The gate for MCP skill content restored into a session with no Skills servers."""
    where = ", ".join(origins)
    if tool_name == SHELL_TOOL_NAME or getattr(tool, "spawns_shell_capable_agent", False) is True:
        return (
            f"This conversation carries MCP skill content (from {where}) restored "
            "from an earlier session, so this call is asked."
        )
    if tool_name == READ_RESOURCE_TOOL:
        return (
            f"Resource read while this conversation carries restored MCP skill "
            f"content (from {where})."
        )
    return None


def relative_path(entry: SkillEntry, uri: str) -> str:
    return uri[len(entry.root) + 1:] if uri.startswith(entry.root + "/") else uri


def _normalise_uri(uri: str) -> str:
    """Collapse ``.`` / ``..`` segments in a URI's path, scheme kept."""
    scheme, sep, rest = uri.partition("://")
    if not sep:
        return posixpath.normpath(uri)
    head, slash, path = rest.partition("/")
    if not slash:
        return uri
    return f"{scheme}://{head}/{posixpath.normpath(path)}"


def _as_read(server: str, uri: str, data: bytes, mime: Optional[str]) -> ResourceRead:
    """Verified bytes back in the shape ``render_read`` takes."""
    if mime is None or is_textual_mime(mime):
        try:
            return ResourceRead(server, uri, [ResourceContent(uri, mime, text=data.decode("utf-8"))])
        except UnicodeDecodeError:
            pass
    blob = base64.b64encode(data).decode("ascii")
    return ResourceRead(server, uri, [ResourceContent(uri, mime, blob=blob)])


def is_binary(data: bytes, mime: Optional[str]) -> bool:
    if mime is not None and not is_textual_mime(mime):
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
