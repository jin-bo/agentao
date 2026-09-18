"""Session persistence — save and restore conversation history.

Pure disk I/O for ``.agentao/sessions/*.json``: no agent / runtime / CLI
imports. Lives under ``embedding/`` because it is a host-side persistence
concern, not part of the inference core.
"""

import json
import logging
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
) -> Tuple[Path, str]:
    """Serialize conversation to disk and rotate old sessions.

    Args:
        project_root: Project directory whose ``.agentao/sessions`` subdir
            should hold the persisted session files. Required, by keyword.

    Returns:
        ``(path, session_id)`` — path of the saved file and the stable session UUID.
    """
    session_dir = _session_dir(project_root)
    session_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()
    sid = session_id or str(_uuid_mod.uuid4())

    created_at = _find_created_at(session_dir, sid) or now.isoformat()
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
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    _rotate_sessions(session_dir)
    return session_file, sid


def persist_agent_session(
    agent: Any,
    session_id: Optional[str] = None,
    *,
    project_root: Path,
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
    )


#: Task description recorded against a restored activation. It is rendered
#: into the ``<active-skills>`` prompt block as ``Task: ...``, so it is
#: model-facing text — one spelling for every restore path.
RESTORE_TASK_DESCRIPTION = "Restored from session"


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

    return (
        data.get("session_id") or session_file.stem,
        data.get("messages", []),
        data.get("model", ""),
        data.get("active_skills", []),
    )


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
