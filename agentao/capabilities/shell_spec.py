"""Which interpreter runs a shell command, and the launch an executor is handed.

Two problems, one module.

**The dialect has to travel with the decision.** A shell command is text, and
what that text *means* depends on which interpreter reads it. The floor in
``agentao.permissions_hardline`` is written for POSIX shell syntax; on Windows
it has always been scanning cmd syntax with POSIX patterns, which does not fail
loudly — it returns a clean result. So the spec names the dialect, the floor
reads it, and the launch carries it, rather than each of them re-deriving an
answer from ``sys.platform`` at a different moment.

**The launch has to be complete.** ``ShellRequest`` used to be a command string
and a directory, which left the executor to work out what would interpret them.
It now carries a :data:`LaunchRequest` naming the whole thing.

Windows defaults to ``%COMSPEC% /c``, unchanged. PowerShell is opt-in through
the user-level ``shell`` block; see ``docs/reference/configuration.md``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, NewType, Optional, Protocol, Union, runtime_checkable

AbsPath = NewType("AbsPath", str)
FrozenEnv = Mapping[str, str]  # the child environment; a MappingProxyType at runtime


class ShellDialect(Enum):
    """The syntax an interpreter reads.

    ``UNKNOWN`` is what a host executor arrives with when it names none — a
    value to refuse on, not one to guess from.
    """

    POSIX = "posix"
    POWERSHELL = "powershell"
    CMD = "cmd"
    UNKNOWN = "unknown"


WINDOWS_ONLY = frozenset({ShellDialect.POWERSHELL, ShellDialect.CMD})


# --------------------------------------------------------------------- verdicts


@dataclass(frozen=True)
class Deny:
    """The floor's only verdict, and no permission rule can mask it."""

    reason: str


@dataclass(frozen=True)
class Pass:
    """Not a decision to run — only the floor declining to refuse."""


Verdict = Union[Deny, Pass]
PASS = Pass()


@dataclass(frozen=True)
class Exhausted:
    """No interpreter could be established for this configuration.

    The shell tool stays registered and its provider exposes this instead of a
    spec, so the floor refuses the call with a reason. Unregistering the tool
    would tell the model that shells do not exist, which is a different and
    worse answer than telling it this call was refused.
    """

    reason: str


# ---------------------------------------------------------------- configuration


@dataclass(frozen=True, kw_only=True)
class ShellBlock:
    """The user-level ``shell`` configuration.

    Never workspace-level. That is a trust boundary rather than a filing
    decision: a rule checked into a repository must not be able to hand the
    agent an interpreter the person running it never approved.

    ``dialect`` alone is legal and is the ordinary way to ask for PowerShell —
    agentao finds the interpreter. ``path`` alone is refused: a renamed
    launcher says nothing about the syntax it reads, and a half-specified block
    is a configuration nobody can read back.
    """

    path: Optional[AbsPath] = None
    dialect: Optional[ShellDialect] = None

    def incomplete(self) -> Optional[str]:
        """``"dialect"`` when a path was given without one, else ``None``."""
        return "dialect" if self.path is not None and self.dialect is None else None


# ------------------------------------------------------------------- the spec


@dataclass(frozen=True, kw_only=True)
class ShellSpec:
    """What will interpret this call's command, frozen for the length of the call.

    ``interpreter`` is an absolute path when one was chosen — configured, or
    found by PowerShell discovery — and ``None`` when the platform's own answer
    stands: ``/bin/bash`` (or ``/bin/sh``) on POSIX, ``%COMSPEC%`` on Windows.
    That ``None`` is not a missing value; it is the launch every unconfigured
    host has always had.
    """

    dialect: ShellDialect
    interpreter: Optional[AbsPath] = None


def validate(spec: ShellSpec) -> Optional[str]:
    """A floor reason for a spec that cannot be used, or ``None``.

    Run at construction *and* again when the floor is entered, on purpose:
    construction covers the specs agentao builds, and the second run covers a
    spec that reached the floor from a host executor without passing through a
    constructor here.
    """
    if not isinstance(spec.dialect, ShellDialect) or spec.dialect is ShellDialect.UNKNOWN:
        return "hardline:unknown-dialect-opaque"
    return None


def default_spec(
    config: Optional[ShellBlock] = None, windows: Optional[bool] = None
) -> Union[ShellSpec, Exhausted]:
    """Resolve the ``shell`` block against this platform, once per executor.

    Four outcomes, and the table in ``docs/reference/configuration.md`` is the
    same one:

    * nothing configured — today's shell, today's floor, on both platforms;
    * ``dialect: cmd`` on Windows / ``dialect: posix`` on POSIX — the same,
      said out loud;
    * ``dialect: powershell`` on Windows — discovery, or a refusal;
    * a ``path`` + ``dialect`` pair — exactly that interpreter, that syntax.

    A dialect this platform cannot run refuses rather than falling back. The
    fallback would be the interesting bug: cmd reading a body written for
    PowerShell does not fail, it means something else.
    """
    block = config if config is not None else ShellBlock()
    if windows is None:
        windows = sys.platform == "win32"

    missing = block.incomplete()
    if missing is not None:
        return Exhausted(
            f"the 'shell' block names a path without a {missing!r}; neither can be "
            "derived from the other"
        )

    dialect = block.dialect or (ShellDialect.CMD if windows else ShellDialect.POSIX)
    if not windows and dialect in WINDOWS_ONLY:
        return Exhausted(
            f"shell.dialect {dialect.value!r} is Windows-only; this host is not Windows"
        )
    if windows and dialect is ShellDialect.POSIX and block.path is None:
        return Exhausted(
            "shell.dialect 'posix' on Windows needs an explicit shell.path: agentao does "
            "not go looking for a POSIX shell there, because the candidates it would find "
            "(Git Bash, WSL, MSYS) differ in path translation and in what they can reach."
        )

    if block.path is not None:
        return ShellSpec(dialect=dialect, interpreter=block.path)
    if dialect is ShellDialect.POWERSHELL:
        from .powershell import CANDIDATES, discover

        found = discover()
        if found is None:
            return Exhausted(
                "shell.dialect 'powershell' is configured but neither "
                f"{' nor '.join(CANDIDATES)} was found in the known install locations or "
                "on PATH. Install PowerShell, or give an explicit shell.path."
            )
        return ShellSpec(dialect=dialect, interpreter=AbsPath(found))
    return ShellSpec(dialect=dialect)


def display_name(spec: Union[ShellSpec, "Exhausted", None], windows: bool) -> str:
    """How to describe the interpreter to the model, in one word.

    The tool's own description is built from this. Telling the model it is
    writing for cmd while PowerShell reads the text is not a cosmetic mismatch:
    it is the model choosing the wrong syntax on every call.
    """
    if isinstance(spec, ShellSpec):
        if spec.interpreter:
            return os.path.basename(spec.interpreter)
        if spec.dialect is ShellDialect.POWERSHELL:
            return "powershell"
    return "cmd" if windows else "sh"


# ------------------------------------------------------------ launch requests


@dataclass(frozen=True, kw_only=True)
class LegacyLaunch:
    """The platform's own shell, invoked the way it always was.

    ``Popen(shell=True)`` with the command as one string: ``/bin/sh -c <cmd>``
    on POSIX (``executable`` replaces ``argv[0]``) and ``{ComSpec} /c <cmd>`` on
    Windows (``executable`` replaces ``ComSpec``). ``executable`` is ``None``
    for an unconfigured host, which is the launch that shipped.
    """

    command: str
    cwd: AbsPath  # this call's working directory
    env: FrozenEnv  # build_child_env(): inherited, minus agentao's own credentials
    executable: Optional[AbsPath] = None


@dataclass(frozen=True, kw_only=True)
class WindowsLaunch:
    """A named interpreter with a built command line — no shell in between.

    ``application_name`` is ``CreateProcessW``'s ``lpApplicationName``, so the
    image is fixed by path rather than resolved from a name at spawn time, and
    ``command_line`` is what the interpreter parses. Two things build one:
    PowerShell, and any *non-cmd* interpreter a ``shell.path`` names on Windows —
    ``LegacyLaunch`` there means ``shell=True``, which composes
    ``{executable} /c "…"``, and ``/c`` is cmd's switch and nobody else's.
    """

    application_name: AbsPath
    command_line: str
    cwd: AbsPath
    env: FrozenEnv


LaunchRequest = Union[LegacyLaunch, WindowsLaunch]


class LaunchRefused(Exception):
    """A refusal raised at the launch rather than returned from the floor.

    Raised, not returned, because it must not reach the model as an ordinary
    tool error — a tool error is something the model retries. Both delivery
    faces catch it and report it the way a floor denial is reported.
    """

    def __init__(self, deny: Deny) -> None:
        super().__init__(deny.reason)
        self.deny = deny


# ------------------------------------------------------------ the decided call


@dataclass(frozen=True)
class DecidedCall:
    """What this call was decided on, frozen together in one record.

    The point is that the launch has no second source for any of it. Binding
    the spec but not the body would leave a channel that decides ``Get-Date``
    and launches other text through the same plan, with the floor's verdict
    bound to the stale input.

    A record whose verdict is a ``Deny`` refuses at the launch too, so the
    permission layer and the tool read one value rather than computing two.
    """

    spec: Union[ShellSpec, Exhausted, None]
    body: str  # the text the floor scanned, byte for byte
    cwd: AbsPath  # the canonical working directory the decision was made against
    verdict: Verdict


@runtime_checkable
class ShellSpecProvider(Protocol):
    """Anything registered under the shell tool's name must answer this.

    The floor gates on the tool *name*, which is its only hook, so a
    replacement tool that does not name its dialect leaves the floor scanning
    one shell's syntax with another's patterns and reporting a clean result.
    """

    @property
    def shell_spec(self) -> Union[ShellSpec, Exhausted]:
        ...
