"""Hook fixtures that behave the same on every platform.

Most hook tests spell their fixture as POSIX shell — ``echo '{"...": ...}'``,
``echo trouble >&2; exit 2``, ``printf 'a\\nb\\n'``. The dispatcher runs a ``command`` hook
through ``shell=True``, which is ``/bin/sh`` on POSIX and ``cmd.exe`` on Windows, and cmd
shares none of that syntax: it does not treat ``'`` as quoting, it keeps the space before a
redirect inside the echoed text, and it has no ``printf``. The first Windows CI run failed
about sixty tests on exactly this.

**The way out is the product's own exec form.** A rule with ``args`` runs without a shell, one
list element per argument — the reference tells authors to use it whenever a hook takes a
path, which is exactly when a shell is what breaks it. Building fixtures that way makes them
portable *and* keeps the dispatcher under test, instead of re-deriving cmd quoting for every
payload.

**What it does not cover.** A test about the shell itself — placeholder expansion by the
shell, or a variable read out of the child environment — has to keep its shell form, and on
Windows that means ``cmd``. Whether agentao should run command hooks under ``sh`` there, the
way the upstream contract does, is an open product question this module does not answer; see
``docs/design/hooks-claude-contract-conformance-plan.md`` §G5, whose own note that "agentao
has no Windows CI job" is what left it unmeasured until now.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

_FIXTURES = Path(tempfile.gettempdir()) / "agentao-hook-fixtures"
_EMITTER = _FIXTURES / "emit.py"

_EMITTER_SOURCE = """\
import json, sys

spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
sys.stdout.write(spec["stdout"])
sys.stderr.write(spec["stderr"])
for path in spec["touch"]:
    open(path, "a", encoding="utf-8").write(spec["touch_text"])
sys.exit(spec["exit"])
"""


def _emitter() -> Path:
    _FIXTURES.mkdir(parents=True, exist_ok=True)
    if not _EMITTER.exists() or _EMITTER.read_text(encoding="utf-8") != _EMITTER_SOURCE:
        _EMITTER.write_text(_EMITTER_SOURCE, encoding="utf-8")
    return _EMITTER


def emitting(
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    touch: Tuple[str, ...] = (),
    touch_text: str = "fired\n",
) -> Tuple[str, List[str]]:
    """``(command, args)`` for a hook that produces exactly this and nothing else.

    The payload travels in a file, so no shell on any platform ever sees a quote of it.
    """
    spec = json.dumps(
        {
            "stdout": stdout,
            "stderr": stderr,
            "exit": exit_code,
            "touch": [str(p) for p in touch],
            "touch_text": touch_text,
        },
        sort_keys=True,
    )
    _FIXTURES.mkdir(parents=True, exist_ok=True)
    path = _FIXTURES / (hashlib.sha256(spec.encode("utf-8")).hexdigest()[:16] + ".json")
    if not path.exists() or path.read_text(encoding="utf-8") != spec:
        path.write_text(spec, encoding="utf-8")
    return sys.executable, [str(_emitter()), str(path)]


def emits_json(payload: str, **kwargs) -> Tuple[str, List[str]]:
    """A hook whose stdout is ``payload`` followed by a newline, as ``echo`` would give."""
    return emitting(stdout=payload + "\n", **kwargs)


def as_kwargs(command) -> dict:
    """``ParsedHookRule`` keyword arguments for either spelling.

    A plain string stays a shell command; an ``(command, args)`` pair becomes the exec form.
    One helper so a fixture can be converted by wrapping its value, without every test file
    growing its own unpacking.
    """
    if isinstance(command, tuple):
        return {"command": command[0], "args": list(command[1])}
    return {"command": command, "args": None}


def as_handler(command, **extra) -> dict:
    """The same, shaped as a ``hooks.json`` handler entry."""
    entry = {"type": "command", **as_kwargs(command), **extra}
    if entry.get("args") is None:
        entry.pop("args")
    return entry


def shell_emits_json(payload: str) -> str:
    """A **shell** command that prints ``payload``, spelled for this platform.

    The exec form is not available everywhere: the ``agentao-v1`` contract is frozen and has
    no ``args`` key, so a v1 fixture has to be a shell command. Printing a file is the one
    thing both shells do without quoting anything — ``cat`` on POSIX, ``type`` on Windows —
    so the payload never passes through a shell's parser at all.
    """
    _FIXTURES.mkdir(parents=True, exist_ok=True)
    path = _FIXTURES / (hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16] + ".txt")
    if not path.exists() or path.read_text(encoding="utf-8") != payload:
        path.write_text(payload, encoding="utf-8")
    if sys.platform == "win32":
        return f'type "{path}"'
    return f"cat '{path}'"
