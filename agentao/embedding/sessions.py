"""Session persistence — save and restore conversation history.

Pure disk I/O for ``.agentao/sessions/*.json``: no agent / runtime / CLI
imports. Lives under ``embedding/`` because it is a host-side persistence
concern, not part of the inference core.
"""

import json
import logging
import os
import re
import uuid as _uuid_mod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

_SESSION_SUBDIR = ".agentao/sessions"
_MAX_SESSIONS = 10
_TITLE_MAX_CHARS = 60


def strip_system_reminders(text: str) -> str:
    """Remove ``<system-reminder>…</system-reminder>`` blocks and trim whitespace."""
    return _SYSTEM_REMINDER_RE.sub("", text).strip()


def _content_to_text(content: Any) -> str:
    """Normalize a message ``content`` field to a single string.

    Handles both shapes the chat path can produce: a plain string, or a
    list of typed blocks (multimodal/tool-use) whose canonical text block
    is ``{"type": "text", "text": "..."}`` — mirroring
    :meth:`MemoryCrystallizer._user_message_text`. Returns ``""`` for
    empty / None / unsupported shapes so callers never run string ops on
    a list (which would raise ``TypeError`` in ``re.sub``).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(p for p in parts if p)
    return ""


def _session_dir(project_root: Path) -> Path:
    """Return the ``.agentao/sessions`` directory for a project.

    ``None`` is refused here, in the one place every entry point funnels
    through, and not only by the signatures: until 0.5.0 it meant "the
    process cwd", so a caller whose own root was unset would read and write
    — and ``delete_all_sessions`` would delete — some other project's
    sessions without a word.
    """
    if project_root is None:
        raise TypeError(
            "project_root is required: pass the project directory whose "
            ".agentao/sessions should be used (there is no implicit "
            "Path.cwd() fallback since 0.5.0)"
        )
    return Path(project_root) / _SESSION_SUBDIR


def _derive_title(messages: List[Dict[str, Any]]) -> str:
    """Return a short title derived from the first user message."""
    for m in messages:
        if m.get("role") == "user":
            # Normalize multimodal (list) content too — an image+text first
            # message would otherwise fall through and persist an empty title.
            content = strip_system_reminders(_content_to_text(m.get("content", "")))
            if content:
                return content[:_TITLE_MAX_CHARS] + ("…" if len(content) > _TITLE_MAX_CHARS else "")
    return ""


def _find_created_at(session_dir: Path, session_id: str) -> Optional[str]:
    """Search existing session files for the earliest `created_at` matching this UUID."""
    for path in session_dir.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("session_id") == session_id:
                return data.get("created_at")
        except (IOError, json.JSONDecodeError):
            continue
    return None


def _parse_session_datetime(value: Any) -> Optional[datetime]:
    """Parse a persisted session timestamp.

    New session files store ISO datetimes. Older files may only have the
    filename-style timestamp, so keep that as a compatibility fallback.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    iso_text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_text)
    except ValueError:
        pass

    for fmt in ("%Y%m%d_%H%M%S_%f", "%Y%m%d_%H%M%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_session_time_local(value: Any) -> str:
    """Format a session timestamp in the machine's local terminal timezone."""
    dt = _parse_session_datetime(value)
    if dt is None:
        return str(value) if value is not None else ""

    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %z")


def save_session(
    messages: List[Dict[str, Any]],
    model: str,
    active_skills: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    *,
    project_root: Path,
    supersedes: Optional[Path] = None,
    mcp_skill_origins: Optional[List[str]] = None,
) -> Tuple[Path, str]:
    """Serialize conversation to disk and rotate old sessions.

    The file is written under a temporary name and renamed into place, so a
    process killed mid-write leaves no half-written ``*.json`` behind.

    Args:
        project_root: Project directory whose ``.agentao/sessions`` subdir
            should hold the persisted session files. Required, by keyword.
        supersedes: A file an earlier save of *this* session wrote, removed
            once the new one is in place and before rotation runs — so saving
            one session repeatedly keeps one file for it instead of one per
            save, and does not evict other sessions to make room. Ignored
            unless it is in this project's session directory and records
            the same ``session_id``.
        mcp_skill_origins: Server labels of MCP skill content the
            conversation carried (docs/design/mcp-skills.md). Written as a
            top-level field only when non-empty; :func:`load_session_record`
            turns it back into a provenance record in the transcript.

    Returns:
        ``(path, session_id)`` — path of the saved file and the stable session UUID.
    """
    session_dir = _session_dir(project_root)
    session_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()
    sid = session_id or str(_uuid_mod.uuid4())

    # A valid ``supersedes`` is this session's own earlier save and already
    # carries its ``created_at``; reading it spares the scan of every session
    # file, which a per-turn save would otherwise pay on each turn.
    earlier = (
        _earlier_save(session_dir, Path(supersedes), sid)
        if supersedes is not None else None
    )
    created_at = (
        (earlier or {}).get("created_at")
        or _find_created_at(session_dir, sid)
        or now.isoformat()
    )
    updated_at = now.isoformat()

    # The name is the clock, and the clock is not a guarantee of uniqueness. Windows
    # reports `datetime.now()` at a much coarser granularity than its six microsecond
    # digits suggest, so two saves in quick succession land on the *same* name and the
    # second silently destroys the first — a session the user could still resume by id
    # is simply gone. POSIX makes that race narrow rather than impossible. Stepping to
    # a free name costs one `exists()` and removes the whole class.
    timestamp = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond:06d}"
    session_file = session_dir / f"{timestamp}.json"
    collision = 0
    while session_file.exists():
        collision += 1
        session_file = session_dir / f"{timestamp}_{collision}.json"

    data = {
        "session_id": sid,
        "title": _derive_title(messages),
        "created_at": created_at,
        "updated_at": updated_at,
        "model": model,
        "active_skills": active_skills or [],
        "messages": messages,
    }
    if mcp_skill_origins:
        data["mcp_skill_origins"] = list(mcp_skill_origins)
    partial = session_file.with_name(session_file.name + ".tmp")
    try:
        with open(partial, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(partial, session_file)
    except BaseException:
        # A failed dump (unserializable content, a full disk, a Ctrl-C) must
        # not strand the partial file — nothing globs ``*.tmp``, so nothing
        # would ever remove it.
        try:
            partial.unlink()
        except OSError:
            pass
        raise

    if earlier is not None and Path(supersedes).resolve() != session_file.resolve():
        try:
            Path(supersedes).unlink()
        except FileNotFoundError:
            pass  # rotated out already
    _rotate_sessions(session_dir)
    return session_file, sid


def _earlier_save(session_dir: Path, path: Path, sid: str) -> Optional[dict]:
    """``path``'s contents if it is a file in ``session_dir`` that saved ``sid``, else ``None``."""
    try:
        if path.resolve().parent != session_dir.resolve():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("session_id") == sid:
        return data
    return None


def persist_agent_session(
    agent: Any,
    session_id: Optional[str] = None,
    *,
    project_root: Path,
    supersedes: Optional[Path] = None,
) -> Tuple[Path, str]:
    """Persist ``agent``'s conversation, deriving model + active skills from it.

    Shared by the CLI session-end hook and the ACP session teardown so the
    agent→disk extraction (``messages`` / ``get_current_model()`` /
    ``skill_manager.get_active_skills()``) lives in exactly one place — adding
    a field to the persisted contract only has to touch this function.
    """
    active_skills = list(agent.skill_manager.get_active_skills().keys())
    return save_session(
        messages=agent.messages,
        model=agent.get_current_model(),
        active_skills=active_skills,
        session_id=session_id,
        project_root=project_root,
        supersedes=supersedes,
        mcp_skill_origins=_mcp_skill_origins(agent, active_skills),
    )


#: Task description recorded against a restored activation. It is rendered
#: into the ``<active-skills>`` prompt block as ``Task: ...``, so it is
#: model-facing text — one spelling for every restore path.
RESTORE_TASK_DESCRIPTION = "Restored from session"


def _mcp_skill_origins(agent: Any, active_skills: List[str]) -> List[str]:
    """Every MCP skill origin the conversation carries, for the session file.

    Saved beside the messages rather than in them, so a restore re-arms the
    gates whatever happened to the transcript: a path that moved skill
    content without its marker, or a host's direct ``activate_skill`` later
    deactivated, which left no message at all. The union of what the live
    session holds, what the transcript names and the saved active skills.
    Never raises; on an error the answer is the unknown origin, so the
    restore errs towards asking.
    """
    from ..skills import provenance

    try:
        origins = set(provenance.skill_origins(getattr(agent, "messages", None) or []))
        origins |= {
            label for label in map(provenance.name_origin, active_skills) if label
        }
        session_origins = getattr(agent, "_mcp_session_origins", None)
        if callable(session_origins):
            origins |= set(session_origins())
    except Exception:  # pragma: no cover - defensive
        logger.exception("could not collect MCP skill origins before saving")
        origins = {provenance.UNKNOWN_ORIGIN}
    return sorted(o for o in origins if isinstance(o, str))


def withheld(labels: Any) -> str:
    """The placeholder for withheld MCP skill content, with its origins.

    The marker keeps the provenance through the next save: a session
    restored, saved and restored again still re-arms the gates for the
    servers the withheld content came from.
    """
    from ..skills.provenance import UNKNOWN_ORIGIN, result_marker

    return MCP_SKILL_WITHHELD + "\n" + result_marker(set(labels) or {UNKNOWN_ORIGIN})


#: What a restored transcript shows in place of MCP skill content.
MCP_SKILL_WITHHELD = (
    "[MCP skill content withheld: it was loaded in an earlier session, and its "
    "approval does not carry over. Activate the skill again to reload it.]"
)


def withhold_mcp_skill_content(messages: Any) -> int:
    """Remove MCP skill content from a restored transcript; return how many messages changed.

    A restored session does not re-activate MCP skills (their approval is the
    old session's, docs/design/mcp-skills.md D5), so nothing in the new
    session holds them — and the §6 gates key on held entries. Left in the
    transcript, a skill's instructions would be in context with no gate on
    the shell. So the content goes until the user approves it again: every
    ``<mcp-skill>`` block, every ``<mcp-skill-file>`` block (a skill file
    read through ``read_skill_file`` or a verified ``read_mcp_resource``), and
    every ``read_skill_file`` result whatever its shape, and — by the call's
    arguments — every read-back of such a result the formatter spilled to
    ``.agentao/tool-outputs/``.

    Replaces changed messages in the list with new dicts rather than editing
    them: ACP replays the loaded history to its client from the same dicts,
    and what the *user* is shown of their own history is not what this
    withholds from the model. Never raises.
    """
    from ..skills import provenance

    changed = 0
    if not isinstance(messages, list):
        return 0
    for index, message in enumerate(messages):
        # By provenance: only results of the three tools that put a loaded
        # skill's content into the transcript (never spilled, so no
        # ``read_file`` of a spill is one). A ``read_file`` of a source file
        # that merely mentions the tag is not skill content.
        if not provenance.is_skill_message(message):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if message.get("name") in ("activate_skill", "read_mcp_resource"):
            new = provenance.BLOCK.sub(
                lambda block: withheld(provenance.content_origins(block.group(0))), content,
            )
            if provenance.WRAPPER.search(new):
                # A wrapper with one end missing — a long result is kept as
                # head + tail, which can drop either tag. Where its content
                # ends cannot be told, so the whole message goes.
                new = withheld(provenance.content_origins(content))
        else:
            new = withheld(provenance.content_origins(content))
        if new != content:
            messages[index] = {**message, "content": new}
            changed += 1
    return changed


def _gate_restored_origins(agent: Any, labels: set) -> None:
    """Keep the gates on for MCP skill content the restored transcript carried.

    The skill results themselves are withheld, but what was derived from
    them is not and cannot be told apart: an assistant message quoting the
    instructions, a summary paraphrasing them, a sub-agent's answer. So the
    origins count as loaded in the new conversation — the shell, shell-
    capable spawns and resource reads are asked, as when the content was
    live — until ``/clear``. With no Skills session, the skill manager's
    restore-only origin set gates the same calls, and marked summaries are
    withheld as well. Never raises.
    """
    from ..skills import provenance

    try:
        manager = getattr(agent, "skill_manager", None)
        mcp_skills = getattr(manager, "mcp_skills", None)
        if mcp_skills is not None:
            mcp_skills.taint(labels, getattr(manager, "mcp_view", None))
            return
        if manager is not None:
            manager.mcp_orphan_origins = set(labels)
        messages = getattr(agent, "messages", None)
        for index, message in enumerate(messages or ()):
            origins = provenance.marker_origins(message)
            if origins:
                messages[index] = {**message, "content": withheld(origins)}
    except Exception:  # pragma: no cover - defensive
        logger.exception("could not gate restored MCP skill origins")


def _leave_outgoing_mcp_skills(agent: Any) -> None:
    """End the outgoing conversation's MCP skill state at a restore.

    An in-process resume (``/sessions resume``) swaps the transcript without
    ``clear_history()``: the outgoing session's MCP activations would stay
    in the volatile prompt, and its held entries and approvals would carry
    the gates and the consent over. A new conversation generation and the
    MCP entries dropped from ``active_skills`` end both. Never raises.
    """
    try:
        manager = getattr(agent, "skill_manager", None)
        # The outgoing transcript's restore-only origins go with it; the
        # incoming one's are set afresh by the caller.
        if isinstance(getattr(manager, "mcp_orphan_origins", None), set):
            manager.mcp_orphan_origins = set()
        mcp_skills = getattr(manager, "mcp_skills", None)
        if mcp_skills is None:
            return
        active = getattr(manager, "active_skills", None)
        if isinstance(active, dict):
            for name in [n for n, info in active.items()
                         if isinstance(info, dict) and info.get("mcp_key") is not None]:
                del active[name]
        if getattr(manager, "mcp_view", None) is None:
            mcp_skills.clear_held()
    except Exception:  # pragma: no cover - defensive
        logger.exception("could not reset MCP skill state on restore")


def restore_agent_skills(
    agent: Any,
    active_skills: Any,
    *,
    session_id: str = "",
    context: str = "resume",
) -> Tuple[List[str], List[str]]:
    """Re-activate a loaded session's skills, one failure at a time.

    The disk→agent counterpart of :func:`persist_agent_session`, and shared
    by every restore path for the same reason that one is shared: the CLI's
    ``/sessions resume``, ACP's ``session/load`` and ACP's startup
    ``--resume`` all have to narrow the same untrusted field and read back
    the same non-exception refusal, and three copies is how one of them ends
    up missing a rule (#271).

    Returns ``(restored, skipped)`` — both drawn from the *narrowed* name
    list, so a caller can report each without re-deriving it. Never raises: a
    load that reached this point has a usable runtime and a hydrated
    transcript, and losing all of that over one stale skill name would be a
    worse outcome than the missing activation this function exists to repair.

    Ways a name does not come back, all logged:

    - **Per skill, refused.** ``activate_skill`` *answers* ``"Error: ..."``
      rather than raising for an unknown *or* a disabled skill (#266), so
      discarding the return value would count a refusal as a success. The
      rest are still tried.
    - **Per skill, raised**, e.g. a host-injected manager with its own rules.
      Likewise isolated.
    - **Whole list: no usable ``skill_manager.activate_skill``** — absent,
      not callable, or an attribute access that itself raises. One warning
      for the list rather than one per name, because it is a property of the
      runtime: a duck-typed embedder's agent is entitled to lack a skill
      manager entirely.

    ``active_skills`` is whatever was on disk, so it is narrowed here rather
    than trusted: :func:`load_session_record` does no validation, and a
    hand-edited file holding a bare string would otherwise be iterated
    character by character and try to activate ``"p"``, ``"d"``, ``"f"`` —
    while a number or ``null`` would raise ``TypeError`` straight out of the
    ``for`` statement, which on the ``--resume`` launch path is fatal.

    ``context`` is a label used only in log lines (e.g. ``"session/load"``,
    ``"resume"``, ``"/sessions resume"``).
    """
    # First, and whatever the skill list holds: the transcript was hydrated
    # before this runs, on every restore path. Origins are read *before* the
    # content is withheld: what the model said about a skill (an assistant
    # message quoting it) stays, so its gates must come back.
    from ..skills import provenance

    try:
        carried = provenance.skill_origins(getattr(agent, "messages", None) or [])
    except Exception:  # pragma: no cover - defensive
        carried = {provenance.UNKNOWN_ORIGIN}
    # And the saved active skills: an MCP skill a host activated directly put
    # its instructions in context through the active-skills block alone,
    # leaving no message — its name on disk is the only record.
    if isinstance(active_skills, (list, tuple)):
        carried |= {
            label for label in map(provenance.name_origin, active_skills) if label
        }
    try:
        withheld_count = withhold_mcp_skill_content(getattr(agent, "messages", None))
    except Exception:  # pragma: no cover - defensive; the helper does not raise
        withheld_count = 0
    if withheld_count:
        logger.info(
            "%s withheld MCP skill content from %d restored message(s) for %s",
            context, withheld_count, session_id or "session",
        )
    _leave_outgoing_mcp_skills(agent)
    if carried:
        _gate_restored_origins(agent, carried)
    if isinstance(active_skills, (list, tuple)):
        names = [n for n in active_skills if isinstance(n, str) and n]
        dropped = len(active_skills) - len(names)
    else:
        # Not a sequence at all, so ``len()`` is not safe to reach for here —
        # a JSON number would raise, out of the one function that promised
        # not to take the load down with it.
        names = []
        dropped = 1 if active_skills else 0
    if dropped:
        logger.warning(
            "%s ignored %d malformed active_skills entr%s for %s (%r)",
            context,
            dropped,
            "y" if dropped == 1 else "ies",
            session_id or "session",
            active_skills,
        )
    if not names:
        return [], []

    # ``getattr(..., None)`` defaults a *missing* attribute; it does not
    # swallow one that raises, and a host's ``skill_manager`` is free to be a
    # property that does. Unguarded, that escapes into the caller's cleanup
    # block and tears down a session that was otherwise fully loaded.
    try:
        activate = getattr(
            getattr(agent, "skill_manager", None), "activate_skill", None
        )
    except Exception:
        logger.exception(
            "%s could not reach skill_manager to restore %d skill(s) for "
            "%s: %s",
            context,
            len(names),
            session_id or "session",
            ", ".join(names),
        )
        return [], list(names)
    if not callable(activate):
        logger.warning(
            "%s cannot restore %d active skill(s) for %s — this runtime has "
            "no skill_manager.activate_skill: %s",
            context,
            len(names),
            session_id or "session",
            ", ".join(names),
        )
        return [], list(names)

    restored: List[str] = []
    skipped: List[str] = []
    for name in names:
        # An MCP skill's approval is for the session that gave it
        # (docs/design/mcp-skills.md D5), and a restore runs outside the
        # planner's consent gate: re-activating here would load
        # server-written instructions — or, for a name absent from the
        # listing, an arbitrary URI from the session file — with no consent.
        if name.startswith("mcp:"):
            logger.info(
                "%s did not restore MCP skill %r for %s: its approval does not "
                "carry over; activate it again to be asked",
                context,
                name,
                session_id or "session",
            )
            skipped.append(name)
            continue
        try:
            outcome = activate(name, RESTORE_TASK_DESCRIPTION)
        except Exception:
            logger.exception(
                "%s could not restore skill %r for %s",
                context,
                name,
                session_id or "session",
            )
            skipped.append(name)
            continue
        # Only an explicit error string is a refusal — a host-injected
        # manager may answer with something else entirely, and that is not
        # this function's business to adjudicate.
        if isinstance(outcome, str) and outcome.startswith("Error"):
            logger.warning(
                "%s did not restore skill %r for %s (disabled, or no longer "
                "discoverable): %s",
                context,
                name,
                session_id or "session",
                outcome.strip(),
            )
            skipped.append(name)
            continue
        restored.append(name)

    logger.info(
        "%s restored %d of %d active skill(s) for %s%s",
        context,
        len(restored),
        len(names),
        session_id or "session",
        f": {', '.join(restored)}" if restored else "",
    )
    return restored, skipped


def _resolve_session_file(
    session_id: Optional[str] = None,
    *,
    project_root: Path,
) -> Path:
    """Resolve a session selector to the on-disk session file.

    ``session_id`` may be a persisted UUID (or prefix), a timestamp prefix,
    or ``None`` for the latest session. Latest-wins ordering and the
    UUID-then-timestamp fallback live here so both :func:`load_session` and
    :func:`load_session_record` share one resolution policy.

    Raises:
        FileNotFoundError: If no sessions exist or the given ID is not found.
    """
    session_dir = _session_dir(project_root)
    if not session_dir.exists():
        raise FileNotFoundError("No sessions directory found")

    sessions = sorted(session_dir.glob("*.json"))
    if not sessions:
        raise FileNotFoundError("No saved sessions found")

    if session_id:
        uuid_matches = []
        for path in sessions:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                persisted = data.get("session_id") or ""
                if isinstance(persisted, str) and persisted.startswith(session_id):
                    uuid_matches.append(path)
            except (IOError, json.JSONDecodeError):
                # One unreadable neighbour must never stop the scan — it is
                # not the file being asked for. ``isinstance`` above covers
                # the shapes that parse and then fail on ``.get`` /
                # ``.startswith``, which this clause does not catch.
                continue
        if uuid_matches:
            return sorted(uuid_matches)[-1]
        ts_matches = [s for s in sessions if s.stem.startswith(session_id)]
        if not ts_matches:
            raise FileNotFoundError(f"Session '{session_id}' not found")
        return ts_matches[-1]

    return sessions[-1]


def load_session_record(
    session_id: Optional[str] = None,
    *,
    project_root: Path,
) -> Tuple[str, List[Dict[str, Any]], str, List[str]]:
    """Load a saved session including its persisted ``session_id``.

    Single source of truth for reading a session file: resolves the
    selector once, opens the file once, and returns everything a caller
    might need. :func:`load_session` is a thin wrapper that drops the id.
    ACP startup-resume uses this directly so it recovers the stable
    identity (needed to register + replay under the original id) without
    a second read.

    Args:
        session_id: UUID string (or prefix), timestamp prefix, or None for latest.
        project_root: Project directory containing the persisted
            ``.agentao/sessions`` subdir. Required, by keyword.

    Returns:
        ``(session_id, messages, model, active_skills)``. The id falls back
        to the file stem for legacy files with no ``session_id`` field, so
        it is always non-empty.

    Raises:
        FileNotFoundError: If no sessions exist or the given ID is not found.
        ValueError: If the resolved file is not valid JSON
            (``json.JSONDecodeError`` subclasses ``ValueError``), or is valid
            JSON that is not an object. The second case has to raise the *same*
            type as the first: a file holding ``[]`` or ``null`` parses fine and
            then dies on ``data.get`` with an ``AttributeError``, which every
            caller's corrupt-file handler is written to miss — including the one
            standing between a bad file and ``agentao --resume`` starting the
            CLI at all.
    """
    session_file = _resolve_session_file(session_id, project_root=project_root)

    with open(session_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"session file {session_file.name} is not a JSON object"
        )

    messages = data.get("messages", [])
    _append_saved_mcp_origins(messages, data.get("mcp_skill_origins"))
    return (
        data.get("session_id") or session_file.stem,
        messages,
        data.get("model", ""),
        data.get("active_skills", []),
    )


def _append_saved_mcp_origins(messages: Any, saved: Any) -> None:
    """Carry a session file's ``mcp_skill_origins`` into its transcript.

    Every restore path reads provenance from the messages
    (:func:`restore_agent_skills`), so the saved field reaches all of them by
    becoming a system record there — only for origins the messages do not
    already name. The field is untrusted: anything present that is not a
    list of non-empty strings reads as the unknown origin, so a damaged
    field still gates. Never raises.
    """
    from ..skills import provenance

    if not isinstance(messages, list) or saved is None or saved == []:
        return
    try:
        if isinstance(saved, list) and saved and all(
            isinstance(label, str) and label for label in saved
        ):
            labels = set(saved)
        else:
            labels = {provenance.UNKNOWN_ORIGIN}
        missing = labels - provenance.skill_origins(messages)
        if missing:
            # A ``user`` message in ``<system-reminder>``, the runtime's form
            # for a note in history: a ``system`` message after the first
            # turn is refused by strict chat templates. Replay and titles
            # skip the block; the marker line is trusted on ``user``.
            messages.append({
                "role": "user",
                "content": (
                    "<system-reminder>\n[MCP skill content from these servers was "
                    "in this conversation's context.]\n"
                    + provenance.result_marker(missing) + "\n</system-reminder>"
                ),
            })
    except Exception:  # pragma: no cover - defensive
        logger.exception("could not read the saved MCP skill origins")


def load_session(
    session_id: Optional[str] = None,
    *,
    project_root: Path,
) -> Tuple[List[Dict[str, Any]], str, List[str]]:
    """Load a saved session.

    Args:
        session_id: UUID string (or prefix), timestamp prefix, or None for latest.
        project_root: Project directory containing the persisted
            ``.agentao/sessions`` subdir. Required, by keyword.

    Returns:
        ``(messages, model, active_skills)``

    Raises:
        FileNotFoundError: If no sessions exist or the given ID is not found.
    """
    _session_id, messages, model, active_skills = load_session_record(
        session_id, project_root=project_root
    )
    return (messages, model, active_skills)


def list_sessions(project_root: Path) -> List[Dict[str, Any]]:
    """Return metadata for all saved sessions, newest first."""
    session_dir = _session_dir(project_root)
    if not session_dir.exists():
        return []

    result = []
    for path in sorted(session_dir.glob("*.json"), reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # A file that parses but is not an object (``[]``, ``null``, a bare
            # string) is corrupt in the only sense this loop cares about, and
            # ``data.get`` would raise ``AttributeError`` — which the skip below
            # does not catch, so one such file took the whole listing down and
            # with it ``/sessions list`` and every ``resume`` that starts here.
            # Same for a ``messages`` value that is not a list of objects.
            if not isinstance(data, dict) or not isinstance(
                data.get("messages", []), list
            ):
                continue
            messages = data.get("messages", [])
            first_user_msg = next(
                (m.get("content", "") for m in messages if m.get("role") == "user"),
                None,
            )
            if first_user_msg:
                # ``content`` may be a multimodal list (image turn); normalize
                # to text before running the system-reminder regex.
                first_user_msg = strip_system_reminders(_content_to_text(first_user_msg))
            if first_user_msg and len(first_user_msg) > 80:
                first_user_msg = first_user_msg[:77] + "..."

            title = data.get("title") or (first_user_msg[:_TITLE_MAX_CHARS] if first_user_msg else "")

            result.append({
                "id": path.stem,
                "session_id": data.get("session_id"),
                "title": title,
                "timestamp": data.get("updated_at") or data.get("timestamp", path.stem),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "model": data.get("model", "unknown"),
                "message_count": len(messages),
                "active_skills": data.get("active_skills", []),
                "path": str(path),
                "first_user_msg": first_user_msg,
            })
        except (IOError, json.JSONDecodeError, AttributeError, TypeError):
            # ``AttributeError`` / ``TypeError``: a session file that parses
            # into the right *shape* but the wrong *types* one level down (a
            # message that is a string, say). Skipping one unreadable file has
            # always been this loop's contract; raising out of it is not.
            continue
    return result


def delete_session(session_id: str, project_root: Path) -> bool:
    """Delete a session by UUID or timestamp prefix.

    Returns:
        True if deleted, False if not found.
    """
    session_dir = _session_dir(project_root)
    if not session_dir.exists():
        return False

    uuid_deleted = 0
    for path in list(session_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            persisted = data.get("session_id") or ""
            if isinstance(persisted, str) and persisted.startswith(session_id):
                path.unlink()
                uuid_deleted += 1
        except (IOError, json.JSONDecodeError):
            continue
    if uuid_deleted:
        return True

    matches = list(session_dir.glob(f"{session_id}*.json"))
    if not matches:
        return False
    matches[0].unlink()
    return True


def delete_all_sessions(project_root: Path) -> int:
    """Delete all saved sessions.

    Returns:
        Number of sessions deleted.
    """
    session_dir = _session_dir(project_root)
    if not session_dir.exists():
        return 0
    count = 0
    for path in session_dir.glob("*.json"):
        path.unlink()
        count += 1
    return count


def _rotate_sessions(session_dir: Path):
    sessions = sorted(session_dir.glob("*.json"))
    while len(sessions) > _MAX_SESSIONS:
        sessions[0].unlink()
        sessions = sessions[1:]
