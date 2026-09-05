"""The trusted table: data, not code.

PR-2 of the PowerShell ladder. Rules ``TOK-01``, ``EFF-01`` through ``EFF-08`` and ``NAME-01``
are defined once each in ``docs/design/powershell-support-spec.zh.md`` §2.

**The shape of the idea.** A command word is trusted only if some entry here says what it
does, and an entry says that by registering *shapes of arguments* rather than by running
logic. Registering no trigger at all is a legal, meaningful entry: it means the command is
inert. What cannot go in the table is a command whose triggers nobody has worked out —
"probably fine" has no spelling here.

**Why an unknown word refuses its own call and not merely what follows.** Tainting only the
rest of the body is a hole one line wide: a single-command script has no rest to taint. So a
word with no entry refuses the call it appears in. That is the expensive half of the design
and the reason the table's coverage is a product decision (question q9, answered 2026-09-05:
the minimal set plus the everyday read-only toolchain, each entry carrying its source).

**Nothing here reaches the filesystem.** Deciding which image a name resolves to, and whether
that image is trusted, is the next stage's work. This module answers only what a name means
and what its arguments imply, which is the half that does not depend on runtime state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Dict, FrozenSet, Optional, Sequence, Tuple, Union

from ..capabilities.shell_spec import ShellDialect


# ------------------------------------------------------------------ TOK-01


@dataclass(frozen=True)
class Literal:
    """A token whose text is known now."""

    text: str


@dataclass(frozen=True)
class Dynamic:
    """A token whose value is decided later, tagged with the node kind that made it so.

    Being opaque is a property of the token and of the AST node kind, per dialect. A single
    optional list of strings cannot carry it, which is why this is a type and not a flag.
    """

    kind: str


Token = Union[Literal, Dynamic]


def text_of(token: Token) -> Optional[str]:
    return token.text if isinstance(token, Literal) else None


# ------------------------------------------------------------------ EFF-01


class EffectFlag(Enum):
    """What a command does beyond producing output. The empty set is inertness."""

    rebinds_after = "rebinds_after"
    executes_input = "executes_input"
    rebinds_caller = "rebinds_caller"


class EntryKind(Enum):
    cmdlet = "cmdlet"
    function = "function"
    alias = "alias"
    internal = "internal"
    builtin = "builtin"
    keyword = "keyword"
    application = "application"


# Entries that run inside the interpreter's own process. Their image is the launcher's, which
# was attested when the spec was built, so they need no separate image resolution.
IN_PROCESS_KINDS: FrozenSet[EntryKind] = frozenset(
    {EntryKind.cmdlet, EntryKind.function, EntryKind.internal, EntryKind.builtin, EntryKind.keyword}
)


class MatchMode(Enum):
    """How an argument shape is compared. Three modes, each one auditable on sight."""

    exact = "exact"  # the token is exactly this
    prefix = "prefix"  # the token starts with this (`-c core.pager=`, `--exec-path=`)
    flag_then_value = "flag_then_value"  # this flag, and the token after it is the payload
    tail_is_command = "tail_is_command"  # everything after the word is itself a command


@dataclass(frozen=True)
class ArgPattern:
    """EFF-08: an argument shape, and the only thing an entry may reason about.

    ``matches`` assumes the caller has already refused a dynamic token in any position the
    entry's inertness depends on. It compares literal shapes, so a dynamic token simply fails
    to match — which reads as "no trigger fired", i.e. inert, and is exactly the wrong answer.
    The refusal has to happen before this is ever called.
    """

    pattern: str
    mode: MatchMode = MatchMode.exact

    def matches(self, args: Sequence[Token]) -> bool:
        if self.mode is MatchMode.tail_is_command:
            return bool(args)
        for index, arg in enumerate(args):
            text = text_of(arg)
            if text is None:
                continue
            if self.mode is MatchMode.exact and text == self.pattern:
                return True
            if self.mode is MatchMode.prefix and text.startswith(self.pattern):
                return True
            if self.mode is MatchMode.flag_then_value and text == self.pattern:
                return index + 1 < len(args)
        return False


@dataclass(frozen=True, kw_only=True)
class TrustedEntry:
    """EFF-08. Every field is a registration; ``flags`` reads them and nothing else."""

    name: str
    dialect: ShellDialect
    kind: EntryKind
    source: str  # where the assertion about this command's effects comes from
    alias_target: Optional[str] = None
    reenters: bool = False  # EFF-07's `=`: this dialect's own evaluator
    execution_triggers: Tuple[ArgPattern, ...] = ()
    rebind_triggers: Tuple[ArgPattern, ...] = ()
    caller_scope: bool = False  # EFF-07's `+`: the effect lands in the *caller's* scope
    predicate_positions: FrozenSet[int] = frozenset()  # EFF-06: dynamic here means opaque

    def flags(self, args: Sequence[Token]) -> FrozenSet[EffectFlag]:
        """EFF-01, derived from the registered fields and from no other source."""
        out = set()
        if any(p.matches(args) for p in self.execution_triggers):
            out.add(EffectFlag.executes_input)
            if self.caller_scope:
                out.add(EffectFlag.rebinds_caller)
        if any(p.matches(args) for p in self.rebind_triggers):
            out.add(EffectFlag.rebinds_after)
            if self.caller_scope:
                out.add(EffectFlag.rebinds_caller)
        return frozenset(out)
        # `caller_scope` has to hang off *both* trigger sets, and that is not symmetry for its
        # own sake. An evaluator has no intrinsic rebind trigger — the rebinding happens inside
        # the body it evaluates — so hanging it off the rebind set alone means the exit state
        # that body produces can never be merged back, and `iex 'Set-Alias git evil'; git status`
        # passes. Giving the evaluator an unconditional rebind trigger instead is the opposite
        # error: `iex 'Get-Date'; git status` would then taint everything after it. The flag
        # itself taints nothing; it only says the evaluated body's exit state is this command's.


@dataclass(frozen=True)
class ExitState:
    """EFF-03: whether this body left a rebound name behind when it finished."""

    tainted: bool = False

    def merge(self, other: "ExitState") -> "ExitState":
        return ExitState(self.tainted or other.tainted)


# ------------------------------------------------------------------ the tables

_EXACT = MatchMode.exact
_PREFIX = MatchMode.prefix
_FLAG_VALUE = MatchMode.flag_then_value
_TAIL = MatchMode.tail_is_command


def _entry(name, kind, source, **kw) -> TrustedEntry:
    return TrustedEntry(name=name, dialect=ShellDialect.POSIX, kind=kind, source=source, **kw)


# The POSIX table. Question q9 chose the minimal set plus the everyday read-only toolchain,
# so what is here is the commands a developer runs constantly and whose effects are checkable
# — not everything that seemed harmless. Each entry names where its claim comes from.
#
# Read this table as a list of assertions someone has to be willing to defend. An entry with
# empty trigger tuples is asserting: given any arguments, this command binds no name, writes
# no environment variable, and runs nothing supplied on its own command line.
_POSIX_ENTRIES: Tuple[TrustedEntry, ...] = (
    # --- inert: read-only inspection, no argument shape changes that
    _entry("ls", EntryKind.application, "coreutils ls(1): lists directory contents"),
    _entry("pwd", EntryKind.builtin, "POSIX pwd: prints the working directory"),
    _entry("echo", EntryKind.builtin, "POSIX echo: writes its arguments to stdout"),
    _entry("cat", EntryKind.application, "coreutils cat(1): concatenates to stdout"),
    _entry("head", EntryKind.application, "coreutils head(1)"),
    _entry("tail", EntryKind.application, "coreutils tail(1)"),
    _entry("wc", EntryKind.application, "coreutils wc(1)"),
    _entry("basename", EntryKind.application, "coreutils basename(1)"),
    _entry("dirname", EntryKind.application, "coreutils dirname(1)"),
    _entry("true", EntryKind.builtin, "POSIX true"),
    _entry("false", EntryKind.builtin, "POSIX false"),
    _entry("date", EntryKind.application, "coreutils date(1); no -s/--set here means read-only",
           rebind_triggers=(ArgPattern("-s", _EXACT), ArgPattern("--set", _PREFIX))),
    # `grep` reads; `-f` reads a pattern file, which is still reading.
    _entry("grep", EntryKind.application, "GNU grep(1): matches and prints, never executes"),
    _entry("rg", EntryKind.application, "ripgrep: matches and prints"),
    _entry("find", EntryKind.application,
           "find(1) is inert only without -exec/-execdir/-delete/-fprint, each of which is registered",
           execution_triggers=(ArgPattern("-exec", _EXACT), ArgPattern("-execdir", _EXACT),
                               ArgPattern("-ok", _EXACT), ArgPattern("-okdir", _EXACT))),
    # --- git: the read-only subcommands are inert; the config surface is not
    _entry("git", EntryKind.application,
           "git(1): -c/--exec-path/-c core.pager= reach a shell; the read-only subcommands do not",
           execution_triggers=(
               ArgPattern("-c", _FLAG_VALUE),  # -c core.pager=<cmd>, -c core.fsmonitor=<cmd>
               ArgPattern("--exec-path=", _PREFIX),
               ArgPattern("--upload-pack", _FLAG_VALUE),
               ArgPattern("--receive-pack", _FLAG_VALUE),
           ),
           rebind_triggers=(ArgPattern("config", _EXACT),)),
    # --- interpreters: in the table, but every way of feeding them code is a trigger
    _entry("python", EntryKind.application, "CPython: -c and -m run supplied code",
           execution_triggers=(ArgPattern("-c", _FLAG_VALUE), ArgPattern("-m", _FLAG_VALUE),
                               ArgPattern("-", _EXACT))),
    _entry("python3", EntryKind.application, "CPython: -c and -m run supplied code",
           execution_triggers=(ArgPattern("-c", _FLAG_VALUE), ArgPattern("-m", _FLAG_VALUE),
                               ArgPattern("-", _EXACT))),
    _entry("node", EntryKind.application, "Node: -e/-p/--eval/--print run supplied code",
           execution_triggers=(ArgPattern("-e", _FLAG_VALUE), ArgPattern("-p", _FLAG_VALUE),
                               ArgPattern("--eval", _FLAG_VALUE), ArgPattern("--print", _FLAG_VALUE))),
    # --- WRAP-07 prefix runners: not wrappers, and never inert.
    # Their whole argv tail is a command, which is a trigger that always fires. Registering
    # one as inert is what lets `timeout 5 ./evil` through, and that is the shape of hole the
    # existing floor was measured to have.
    _entry("timeout", EntryKind.application, "WRAP-07: runs its argv tail",
           execution_triggers=(ArgPattern("", _TAIL),)),
    _entry("nice", EntryKind.application, "WRAP-07: runs its argv tail",
           execution_triggers=(ArgPattern("", _TAIL),)),
    _entry("nohup", EntryKind.application, "WRAP-07: runs its argv tail",
           execution_triggers=(ArgPattern("", _TAIL),)),
    _entry("env", EntryKind.application, "WRAP-07: runs its argv tail, and assigns variables first",
           execution_triggers=(ArgPattern("", _TAIL),)),
    _entry("sudo", EntryKind.application, "WRAP-07: runs its argv tail as another user",
           execution_triggers=(ArgPattern("", _TAIL),)),
    _entry("xargs", EntryKind.application, "WRAP-07: runs its argv tail once per input line",
           execution_triggers=(ArgPattern("", _TAIL),)),
    _entry("command", EntryKind.builtin, "WRAP-07: runs its argv tail",
           execution_triggers=(ArgPattern("", _TAIL),)),
    _entry("exec", EntryKind.builtin, "WRAP-07: replaces the shell with its argv tail",
           execution_triggers=(ArgPattern("", _TAIL),)),
    _entry("watch", EntryKind.application, "WRAP-07: runs its argv tail repeatedly",
           execution_triggers=(ArgPattern("", _TAIL),)),
    # --- EFF-07b: the enumerated ways a POSIX shell rebinds a name for what follows
    _entry("export", EntryKind.builtin, "EFF-07b: binds an environment variable for the rest of the body",
           rebind_triggers=(ArgPattern("", _TAIL),)),
    _entry("alias", EntryKind.builtin, "EFF-07b: rebinds a command word for the rest of the body",
           rebind_triggers=(ArgPattern("", _TAIL),)),
    _entry("declare", EntryKind.builtin, "EFF-07b: -x exports",
           rebind_triggers=(ArgPattern("-x", _EXACT),)),
    _entry("hash", EntryKind.builtin, "EFF-07b: -p rebinds a command word to a path",
           rebind_triggers=(ArgPattern("-p", _EXACT),)),
    _entry("read", EntryKind.builtin, "EFF-07b: binds a variable from stdin",
           rebind_triggers=(ArgPattern("", _TAIL),)),
    _entry("source", EntryKind.builtin,
           "EFF-07: runs a file in the caller's scope; a file target is always opaque (EFF-02)",
           execution_triggers=(ArgPattern("", _TAIL),), caller_scope=True),
    _entry(".", EntryKind.builtin,
           "EFF-07: runs a file in the caller's scope; a file target is always opaque (EFF-02)",
           execution_triggers=(ArgPattern("", _TAIL),), caller_scope=True),
    _entry("eval", EntryKind.builtin,
           "EFF-07 `=`: the shell's own evaluator, and the input language is this dialect",
           execution_triggers=(ArgPattern("", _TAIL),), caller_scope=True, reenters=True),
)


def _cmd_entry(name, kind, source, **kw) -> TrustedEntry:
    return TrustedEntry(name=name, dialect=ShellDialect.CMD, kind=kind, source=source, **kw)


# The cmd table is small on purpose. CMD-01 already refuses every body containing a control
# keyword, a grouping parenthesis or any variable form, so most of what would need an entry
# never reaches this point. What remains is the plain internal commands.
_CMD_ENTRIES: Tuple[TrustedEntry, ...] = (
    _cmd_entry("echo", EntryKind.internal, "cmd help: writes its arguments"),
    _cmd_entry("dir", EntryKind.internal, "cmd help: lists a directory"),
    _cmd_entry("type", EntryKind.internal, "cmd help: writes a file to stdout"),
    _cmd_entry("cd", EntryKind.internal, "cmd help: changes the working directory"),
    _cmd_entry("ver", EntryKind.internal, "cmd help: prints the version"),
    _cmd_entry("vol", EntryKind.internal, "cmd help: prints the volume label"),
    _cmd_entry("where", EntryKind.application, "where.exe: locates a file on PATH, no execution"),
    # EFF-07b, cmd row: the enumerated ways cmd rebinds a name for the lines that follow.
    _cmd_entry("set", EntryKind.internal, "EFF-07b: binds an environment variable",
               rebind_triggers=(ArgPattern("", _TAIL),)),
    _cmd_entry("path", EntryKind.internal, "EFF-07b: rebinds PATH itself",
               rebind_triggers=(ArgPattern("", _TAIL),)),
    _cmd_entry("setx", EntryKind.application, "EFF-07b: binds an environment variable persistently",
               rebind_triggers=(ArgPattern("", _TAIL),)),
    _cmd_entry("assoc", EntryKind.internal, "EFF-07b: rebinds an extension to a file type",
               rebind_triggers=(ArgPattern("", _TAIL),)),
    _cmd_entry("ftype", EntryKind.internal, "EFF-07b: rebinds a file type to a command",
               rebind_triggers=(ArgPattern("", _TAIL),)),
)


def _ps_entry(name, kind, source, **kw) -> TrustedEntry:
    return TrustedEntry(name=name, dialect=ShellDialect.POWERSHELL, kind=kind, source=source, **kw)


# The PowerShell table. Verbs are the unit here: `Get-*` reads, `Set-*` and `New-*` bind.
_PS_ENTRIES: Tuple[TrustedEntry, ...] = (
    _ps_entry("Get-Date", EntryKind.cmdlet, "PowerShell Get-Date: returns the current time"),
    _ps_entry("Get-Location", EntryKind.cmdlet, "PowerShell Get-Location"),
    _ps_entry("Get-ChildItem", EntryKind.cmdlet, "PowerShell Get-ChildItem: enumerates a provider path"),
    _ps_entry("Get-Content", EntryKind.cmdlet, "PowerShell Get-Content: reads a file"),
    _ps_entry("Select-String", EntryKind.cmdlet, "PowerShell Select-String: matches and prints"),
    _ps_entry("Write-Output", EntryKind.cmdlet, "PowerShell Write-Output"),
    _ps_entry("Measure-Object", EntryKind.cmdlet, "PowerShell Measure-Object"),
    # EFF-07, PowerShell row. `iex` is the dialect's own evaluator, so it is the one entry that
    # may re-enter a literal string; `+` on both means the effect lands in the caller's scope.
    _ps_entry("Invoke-Expression", EntryKind.cmdlet, "EFF-07 `+=`: this dialect's evaluator",
              execution_triggers=(ArgPattern("", _TAIL),), caller_scope=True, reenters=True),
    _ps_entry("iex", EntryKind.alias, "alias of Invoke-Expression", alias_target="Invoke-Expression",
              execution_triggers=(ArgPattern("", _TAIL),), caller_scope=True, reenters=True),
    _ps_entry("Import-Module", EntryKind.cmdlet, "EFF-07 `+`: loads and runs a module in the caller's scope",
              execution_triggers=(ArgPattern("", _TAIL),), caller_scope=True),
    _ps_entry("ipmo", EntryKind.alias, "alias of Import-Module", alias_target="Import-Module",
              execution_triggers=(ArgPattern("", _TAIL),), caller_scope=True),
    # NAME-02's ordering is not a corner case: `mkdir`, `more` and `help` are *functions*
    # under `-NoProfile`, so a function entry has to be registrable. `mkdir` wraps
    # `New-Item -ItemType Directory`, which cannot reach the `Function:` or `Alias:` providers
    # `New-Item` is registered for — so it binds no name and runs nothing off its own line.
    _ps_entry("mkdir", EntryKind.function,
              "PowerShell mkdir: a function wrapping New-Item -ItemType Directory"),
    _ps_entry("Add-Type", EntryKind.cmdlet, "EFF-07 `+`: compiles and loads supplied source",
              execution_triggers=(ArgPattern("", _TAIL),), caller_scope=True),
    # EFF-07b, PowerShell row: the enumerated rebinding forms.
    _ps_entry("Set-Alias", EntryKind.cmdlet, "EFF-07b: rebinds a command word",
              rebind_triggers=(ArgPattern("", _TAIL),)),
    _ps_entry("New-Alias", EntryKind.cmdlet, "EFF-07b: binds a command word",
              rebind_triggers=(ArgPattern("", _TAIL),)),
    _ps_entry("Set-Variable", EntryKind.cmdlet, "EFF-07b: binds a variable",
              rebind_triggers=(ArgPattern("", _TAIL),)),
    _ps_entry("Set-Content", EntryKind.cmdlet, "EFF-07b: writes a file, which a later command may run",
              rebind_triggers=(ArgPattern("", _TAIL),)),
    _ps_entry("Set-Item", EntryKind.cmdlet, "EFF-07b: writes through a provider, including Env: and Alias:",
              rebind_triggers=(ArgPattern("", _TAIL),)),
    _ps_entry("New-Item", EntryKind.cmdlet, "EFF-07b: creates through a provider, including Function:",
              rebind_triggers=(ArgPattern("", _TAIL),)),
)


def _index(entries: Tuple[TrustedEntry, ...], fold: bool) -> Dict[str, TrustedEntry]:
    """Name to entry. ``fold`` because cmd and PowerShell match command words case-insensitively."""
    return {(e.name.lower() if fold else e.name): e for e in entries}


def _as_dialect(entries: Tuple[TrustedEntry, ...], dialect: ShellDialect) -> Tuple[TrustedEntry, ...]:
    """The same external programs, registered under another dialect.

    IMG-02's name half asks whether the word is in **that dialect's** trusted table, so each
    dialect needs its own entries. What it does not need is its own *claims*: ``git`` is the
    same program with the same argument shapes and the same effects whichever interpreter
    spelled the command line, and the parsing that differs has already happened by the time a
    word reaches this table. So the applications are defined once and instantiated per
    dialect. Three hand-maintained copies would drift, and the drift would be silent — an
    argument trigger added to the POSIX ``git`` and forgotten on the PowerShell one is a
    Windows bypass nobody sees.

    In-process entries are not shared: a cmdlet, a cmd internal command and a bash builtin are
    each part of one interpreter and have no meaning in another.
    """
    return tuple(
        replace(entry, dialect=dialect)
        for entry in entries
        if entry.kind is EntryKind.application
    )


_APPLICATIONS: Tuple[TrustedEntry, ...] = tuple(
    e for e in _POSIX_ENTRIES if e.kind is EntryKind.application
)

_TABLES: Dict[ShellDialect, Dict[str, TrustedEntry]] = {
    ShellDialect.POSIX: _index(_POSIX_ENTRIES, fold=False),
    ShellDialect.CMD: _index(
        _CMD_ENTRIES + _as_dialect(_APPLICATIONS, ShellDialect.CMD), fold=True
    ),
    ShellDialect.POWERSHELL: _index(
        _PS_ENTRIES + _as_dialect(_APPLICATIONS, ShellDialect.POWERSHELL), fold=True
    ),
}

_FOLDED: FrozenSet[ShellDialect] = frozenset({ShellDialect.CMD, ShellDialect.POWERSHELL})


def lookup(word: str, dialect: ShellDialect) -> Optional[TrustedEntry]:
    """NAME-01 / NAME-02: the entry a command word resolves to, or ``None``.

    ``None`` is the expensive answer: EFF-04 makes it refuse the call the word appears in,
    not merely taint what follows. That is deliberate — tainting only the remainder is a hole
    one line wide, because a single-command body has no remainder.
    """
    table = _TABLES.get(dialect)
    if table is None:
        return None
    return table.get(word.lower() if dialect in _FOLDED else word)


def entries_for(dialect: ShellDialect) -> Tuple[TrustedEntry, ...]:
    """Every registered entry for a dialect, for tests and for a coverage report."""
    return tuple(_TABLES.get(dialect, {}).values())


_PROVIDER_DRIVE = re.compile(r"^([A-Za-z][A-Za-z0-9]*):")


def names_provider_drive(args: Sequence[Token]) -> bool:
    """EFF-05: whether any argument names a non-filesystem PowerShell provider drive.

    One rule closes `Env:`, `Alias:`, `Function:`, `Variable:` and the registry drives at once,
    whatever the cmdlet is. A drive-letter path such as `C:\\x` is a filesystem path and stays
    inert — the shape that matters is a provider name, which is more than one letter.
    """
    for arg in args:
        text = text_of(arg)
        if text is None:
            continue
        m = _PROVIDER_DRIVE.match(text)
        if m is not None and len(m.group(1)) > 1:
            return True
    return False
