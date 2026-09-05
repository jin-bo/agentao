r"""Trusted resolution: who may be launched, from where, and with what environment.

PR-4 of the PowerShell ladder. ``IMG-01`` through ``IMG-09``, ``ENV-01`` through ``ENV-06``,
``LAUNCH-02`` through ``LAUNCH-09``, ``LADDER-01``, ``LADDER-03``, ``NAME-02``, ``NAME-03``,
``SPEC-04a`` and ``SPEC-05`` are each defined once in
``docs/design/powershell-support-spec.zh.md`` §2, and transcribed as typed signatures in
``docs/design/powershell-support-contracts.py``.

**One predicate, three consumers.** IMG-01 asks a single question — *can the token this child
will run as modify, delete, replace or rename this path, or any ancestor of it up to the
volume root?* Interpreter selection, the image half of the closed runnable set, and the
child's ``PATH`` filter are all that same question asked about different paths. Writing it
three times is writing three chances to answer it differently.

**Everything the answer depends on comes from the oracle, and the oracle is bound to one
subject** (SPEC-05). Access masks, reparse points, signatures, the target's base environment
and its project root are facts about the machine the command will run on, which is not
necessarily the machine the floor is running on. A remote executor supplies its own oracle;
the local one answers for this host. Neither is consulted for a policy-off rung, because
there is nothing there to attest.

**Nothing here is reachable in production yet.** Every rung constructible today is policy-off
(``LADDER-05``), so :func:`select_rung` returns through its two legacy branches and the
attested path below them is exercised only by tests with an injected oracle — which is what
every PR-4 gate row in ``docs/design/powershell-support-gates.zh.md`` does.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import (
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)

from ..capabilities.shell_spec import (
    POLICY_OFF_RUNGS,
    POWERSHELL_RUNGS,
    AbsPath,
    Allowlist,
    Exhausted,
    FrozenEnv,
    GIT_BASH_RELEASED,
    HashPin,
    InterpreterIdentity,
    LADDER_FLIPPED,
    LauncherIdentity,
    LegacyLaunch,
    PinnedEnv,
    Platform,
    PosixLaunch,
    PublisherTrust,
    ResolvedImage,
    Rung,
    Sha256,
    ShellBlock,
    ShellDialect,
    ShellSpec,
    Subject,
    WindowsLaunch,
    dialect_of,
    fingerprint_of,
    fingerprint_projection,
    legacy_spec,
    validate,
)

AttestedLaunch = Union[PosixLaunch, WindowsLaunch]


# ------------------------------------------------------------------ IMG-06c


class ReparseState(Enum):
    """IMG-06c: three states, because ``AbsPath | None`` cannot hold the third.

    ``None`` meant both "this is not a reparse point" and "I could not tell", and a chain
    nobody could read was then walked as though it had been read and found ordinary.
    """

    not_reparse = "not_reparse"
    resolved = "resolved"
    error = "error"


@dataclass(frozen=True)
class ReparseResult:
    state: ReparseState
    target: Optional[AbsPath] = None


@dataclass(frozen=True)
class SessionConfig:
    """IMG-08: the effective configuration synthesised from the three sources on disk."""

    session: Optional[str] = None  # None = no console session configuration in any source


# ------------------------------------------------------------------ the oracle


class IdentityOracle(Protocol):
    """IMG-06: the host-side answers every trust question here is built out of.

    Bound to one execution subject (SPEC-05). Every method that takes a subject refuses to
    answer for a different one — an oracle that answered about some other token would be
    attesting the wrong machine's files to the wrong process.
    """

    def canonicalize(self, path: str) -> Optional[AbsPath]: ...
    def subject_can_replace(self, path: AbsPath, subject: Subject) -> bool: ...
    def resolve_reparse(self, path: AbsPath) -> ReparseResult: ...
    def resolves_on_target(self, path: AbsPath) -> bool: ...
    def publisher_trusted(self, path: AbsPath) -> bool: ...
    def image_signer(self, path: AbsPath) -> Optional[str]: ...
    def content_hash(self, path: AbsPath) -> Sha256: ...
    def target_base_env(self, subject: Subject) -> Optional[Mapping[str, str]]: ...
    def target_path_entries(self, subject: Subject) -> Optional[Tuple[AbsPath, ...]]: ...
    def target_project_root(self) -> Optional[AbsPath]: ...
    def target_platform(self) -> Platform: ...
    def target_filesystem_is_local(self) -> Optional[bool]: ...
    def target_pinned_env(self, subject: Subject) -> Optional[PinnedEnv]: ...
    def resolve_image(self, path: AbsPath, subject: Subject) -> Optional[ResolvedImage]: ...
    def discover(self, rung: Rung, subject: Subject) -> Optional[ResolvedImage]: ...
    def read_identity(
        self, img: ResolvedImage, dialect: ShellDialect
    ) -> Optional[LauncherIdentity]: ...
    def resolve_pshome(self, img: ResolvedImage) -> Optional[AbsPath]: ...
    def read_config_sources(self, pshome: AbsPath, subject: Subject) -> SessionConfig: ...
    def preflight(self, identity: InterpreterIdentity, prelude: str) -> bool: ...


ORACLE_METHODS: Tuple[str, ...] = (
    "canonicalize", "subject_can_replace", "resolve_reparse", "resolves_on_target",
    "publisher_trusted", "image_signer", "content_hash", "target_base_env",
    "target_path_entries", "target_project_root", "target_platform",
    "target_filesystem_is_local", "target_pinned_env", "resolve_image", "discover",
    "read_identity", "resolve_pshome", "read_config_sources", "preflight",
)

SELECTION_METHODS: Tuple[str, ...] = ("target_platform",)
"""Choosing a rung needs exactly this one answer; locality has a default (SPEC-04a)."""


def oracle_answers(oracle: Optional[IdentityOracle], methods: Sequence[str]) -> bool:
    return oracle is not None and all(callable(getattr(oracle, m, None)) for m in methods)


def target_is_local(oracle: IdentityOracle) -> bool:
    """SPEC-04a: cannot answer reads as ``False`` — the stricter side — never as a refusal.

    Both the missing method and an explicit ``None`` land here. Making this question part of
    ``SELECTION_METHODS`` instead would empty the ladder for a default POSIX executor that
    simply does not implement it, and LADDER-03 turns an empty ladder into a denial on every
    shell call — the opposite of LADDER-05's "identical to today".
    """
    if not oracle_answers(oracle, ("target_filesystem_is_local",)):
        return False
    return oracle.target_filesystem_is_local() is True


def oracle_complete(oracle: Optional[IdentityOracle]) -> bool:
    """SPEC-05c: an oracle missing any method leaves the rung unattested.

    ``Protocol`` is a static shape, not a runtime contract. A non-local executor hands in its
    own object, and a missing method is invisible to a type checker — it would surface as an
    ``AttributeError`` inside ``launch()``, long after this call was decided to be allowed,
    and an exception is not a verdict on the DENY channel.

    It constrains **policy-on rungs only.** The two policy-off rungs never ask the oracle
    anything, so gating rung *selection* on completeness would empty the ladder for an
    executor that is behaving exactly as LADDER-05 promises.
    """
    return oracle_answers(oracle, ORACLE_METHODS)


# ------------------------------------------------------- paths, by the target's rules


def _segments(path: str, target: Platform) -> List[str]:
    """The path's segments, folded the way the *target* compares them.

    Windows is case-insensitive and treats ``/`` and ``\\`` alike; POSIX is neither. Reading
    the host's rules here would compare a remote target's paths by this machine's habits.
    """
    text = path.replace("\\", "/") if target is Platform.WINDOWS else path
    parts = [p for p in text.split("/") if p]
    return [p.lower() for p in parts] if target is Platform.WINDOWS else parts


def same_path(a: str, b: str, target: Platform) -> bool:
    """Two canonical paths naming the same thing, by the target's comparison rules."""
    return _segments(a, target) == _segments(b, target)


def path_within(path: AbsPath, root: AbsPath, target: Platform) -> bool:
    r"""Containment by path *segment*, never by string prefix.

    ``C:\repo-evil`` starts with ``C:\repo`` as a string and is not inside it. This is the one
    predicate behind IMG-05a's "outside the project root" and ENV-01's "inside the working
    directory or the project root", so a prefix test here would be wrong in both directions at
    once.
    """
    root_parts = _segments(root, target)
    if not root_parts:
        return False
    return _segments(path, target)[: len(root_parts)] == root_parts


def ancestors_to_volume_root(path: AbsPath, target: Platform) -> Tuple[AbsPath, ...]:
    r"""Every ancestor of ``path`` from its parent up to and including the volume root.

    Takes the target platform rather than sniffing the string: ``C:\a`` and ``/a`` are told
    apart by the rules of the machine the child runs on, and this walk feeds IMG-01, whose
    whole point is that it is asked about the target's filesystem (SPEC-05). Re-asking the
    oracle for the platform instead would break the one-call snapshot G18-14 pins.
    """
    if target is Platform.WINDOWS:
        text = path.replace("/", "\\")
        if text.startswith("\\\\"):  # UNC: the share root is \\server\share
            parts = [p for p in text.split("\\") if p]
            if len(parts) < 2:
                return ()
            root = "\\\\" + "\\".join(parts[:2])
            out = []
            for stop in range(len(parts) - 1, 1, -1):
                out.append(AbsPath("\\\\" + "\\".join(parts[:stop])))
            out.append(AbsPath(root))
            return tuple(dict.fromkeys(out))
        parts = [p for p in text.split("\\") if p]
        if not parts:
            return ()
        drive_root = AbsPath(parts[0] + "\\")
        out = []
        for stop in range(len(parts) - 1, 1, -1):
            out.append(AbsPath("\\".join(parts[:stop])))
        out.append(drive_root)
        return tuple(dict.fromkeys(out))
    parts = [p for p in path.split("/") if p]
    out = []
    for stop in range(len(parts) - 1, 0, -1):
        out.append(AbsPath("/" + "/".join(parts[:stop])))
    out.append(AbsPath("/"))
    return tuple(dict.fromkeys(out))


# ------------------------------------------------------------------ IMG-01


MAX_REPARSE_DEPTH = 32
"""IMG-06c: a ceiling on the reparse chain. A junction cycle is caught by ``following``;
this catches a chain that is merely absurd, and neither is allowed to recurse forever."""


def trusted_root_chain(
    path: AbsPath,
    subject: Subject,
    oracle: IdentityOracle,
    target: Platform,
    following: FrozenSet[AbsPath] = frozenset(),
    depth: int = 0,
) -> bool:
    r"""IMG-01: the subject can change neither this path nor any ancestor of it.

    ``following`` is the entry set of **this reparse walk**, not "ancestors already checked".
    Collapsing the two rejects a trusted junction that points at its own parent
    (``C:\Trusted\alias`` -> ``C:\Trusted``): the parent entered the set while checking the
    alias, and following the junction meets it immediately. That chain resolves, passes every
    question, and would be excluded from launcher selection and from the child's ``PATH``.
    """
    if depth > MAX_REPARSE_DEPTH or path in following:
        return False  # a cycle or an absurd chain fails closed; it is not "already checked, fine"
    chain = (path, *ancestors_to_volume_root(path, target))
    if any(oracle.subject_can_replace(p, subject) for p in chain):
        return False
    for p in chain:
        result = oracle.resolve_reparse(p)
        if result.state is ReparseState.error:
            return False  # unreadable is not "not a reparse point"
        if result.state is ReparseState.resolved:
            if result.target is None or not trusted_root_chain(
                result.target, subject, oracle, target, following | {path}, depth + 1
            ):
                return False
    return True


def allowlist_entry_for(allowlist: Allowlist, path: AbsPath) -> Optional[HashPin]:
    r"""IMG-03: the content pin naming this path, if the host wrote one.

    Exact comparison, and deliberately the same comparison :meth:`HashPin.matches` makes.
    IMG-06b's model is that both sides are already canonical — the image because it came back
    from ``canonicalize``, the pin because a person wrote an absolute path — so one rule
    compares them in both places. Looking the pin up by the target's looser path rules and
    then confirming it by string equality would be two answers to one question, and a pin
    spelled ``c:\pwsh.exe`` would make the image *untrusted* rather than simply unpinned.

    The cost is that a non-canonical pin silently does not apply. Nothing here can fix that:
    canonicalising an allowlist needs the oracle, and the permission loader that reads the
    block has none. It is recorded as an open question rather than papered over.
    """
    for entry in allowlist:
        if isinstance(entry, HashPin) and entry.path == path:
            return entry
    return None


def trusted_image(
    img: ResolvedImage,
    subject: Subject,
    allowlist: Allowlist,
    oracle: IdentityOracle,
    target: Platform,
) -> bool:
    """IMG-01 + IMG-02's image half + IMG-03.

    Takes the allowlist itself, never "the block in force": the one that was in force when the
    decision was made is frozen onto the spec (IMG-03a), and a second readable source would
    let the configuration change between the decision and the launch without the fingerprint
    noticing.
    """
    if not oracle.resolves_on_target(img.canonical_path):
        return False
    if not trusted_root_chain(img.canonical_path, subject, oracle, target):
        return False
    pin = allowlist_entry_for(allowlist, img.canonical_path)
    return pin is None or pin.matches(img)


def host_identity_ok(
    img: ResolvedImage, allowlist: Allowlist, oracle: IdentityOracle
) -> bool:
    """IMG-05 (a): the host-side identity check that happens before any launch, three routes.

    A program cannot be authenticated by running it — running it is the event this check
    exists to gate.
    """
    if oracle.publisher_trusted(img.canonical_path):
        return True  # (1) the host's own trust store
    signer = oracle.image_signer(img.canonical_path)  # (2) a signer the allowlist names
    if signer is not None and any(
        isinstance(e, PublisherTrust) and e.signer == signer for e in allowlist
    ):
        return True
    pin = allowlist_entry_for(allowlist, img.canonical_path)  # (3) path + content hash
    return pin is not None and pin.matches(img)


# ------------------------------------------------------------------ ENV-01, ENV-06


@dataclass(frozen=True, kw_only=True)
class EnvInputs:
    """ENV-06: every external input :func:`child_env` reads, read once per call.

    Named as a record rather than fetched inside the function because "read once" is the
    whole property: the length guard measures one environment and the launch hands over
    another the moment either of them can re-read the world.
    """

    base: Mapping[str, str]
    path_entries: Tuple[AbsPath, ...]
    cwd: AbsPath
    project_root: AbsPath


def read_env_inputs(spec: ShellSpec, cwd: AbsPath) -> Union[EnvInputs, Exhausted]:
    """ENV-06's inputs, all of them from the oracle bound to this spec's subject.

    The contract describes a local branch (this process's environment) and a non-local one
    (the oracle's target answers). They are one branch here, and the local oracle is what
    makes the local answers local — two code paths computing the same three values are two
    chances for the request to carry a value from the floor's machine, which is exactly what
    G24-10 asserts never happens.
    """
    oracle = spec.identity_oracle
    if not oracle_complete(oracle):  # type: ignore[arg-type]
        return Exhausted("SPEC-05c: incomplete oracle")
    subject = spec.execution_subject
    base = oracle.target_base_env(subject)  # type: ignore[union-attr]
    if base is None:
        return Exhausted("ENV-06: no base environment for this subject")
    entries = oracle.target_path_entries(subject)  # type: ignore[union-attr]
    if entries is None:
        return Exhausted("ENV-01: no PATH entries for this subject")
    project_root = oracle.target_project_root()  # type: ignore[union-attr]
    if project_root is None:
        return Exhausted("SPEC-05a: no project root on the target")
    return EnvInputs(
        base=dict(base), path_entries=tuple(entries), cwd=cwd, project_root=project_root
    )


def filtered_path_entries(
    subject: Subject,
    entries: Sequence[AbsPath],
    cwd: AbsPath,
    project_root: AbsPath,
    target: Platform,
    oracle: IdentityOracle,
) -> Tuple[AbsPath, ...]:
    """ENV-01: the directories the subject cannot write, in order, canonical and deduplicated.

    Canonicalisation comes first and is not a tidying step: a ``PATH`` entry is a raw string
    from an environment, and ``path_within`` compares two *canonical* paths. Without it,
    ``..``, an 8.3 short name or a symlink walks straight past both containment tests —
    ``/usr/local/../home/me/bin`` is the working example, and it is the same directory as
    ``/home/me/bin`` however differently it reads.

    Searching is agentao's own: ``shutil.which`` searches the current directory first on
    Windows, which is the one directory this filter exists to keep out.
    """
    kept: List[AbsPath] = []
    seen: List[AbsPath] = []
    for raw in entries:
        if not raw or not str(raw).strip():
            continue
        canonical = oracle.canonicalize(str(raw))
        if canonical is None:
            continue  # unanswerable, an ADS, or unresolvable: it does not go in the child's PATH
        if not _is_absolute(canonical, target):
            continue
        if path_within(canonical, cwd, target) or path_within(canonical, project_root, target):
            continue
        if any(same_path(canonical, s, target) for s in seen):
            continue
        seen.append(canonical)
        if trusted_root_chain(canonical, subject, oracle, target):
            kept.append(canonical)
    return tuple(kept)


def _is_absolute(path: str, target: Platform) -> bool:
    if target is Platform.WINDOWS:
        text = path.replace("/", "\\")
        return text.startswith("\\\\") or (
            len(text) >= 3 and text[0].isalpha() and text[1] == ":" and text[2] == "\\"
        )
    return path.startswith("/")


def join_path(entries: Sequence[AbsPath], target: Platform) -> str:
    return (";" if target is Platform.WINDOWS else ":").join(entries)


def pinned_psmodulepath(spec: ShellSpec) -> str:
    """ENV-05: the install root's module directory, and only if it satisfies IMG-01.

    Defence in depth, not a mechanism — the launcher recomposes ``PSModulePath`` at startup,
    so what agentao hands in is an input rather than a setting. Module auto-loading is off by
    the prelude; this keeps the directory list from naming somewhere the subject can write.

    The oracle and the target platform come off the spec, both frozen at construction, so
    this asks the target's platform question zero extra times (G18-14).
    """
    launcher = spec.launcher
    oracle = spec.identity_oracle
    if not isinstance(launcher, InterpreterIdentity) or not oracle_complete(oracle):  # type: ignore[arg-type]
        return ""
    if not launcher.pshome:
        return ""
    separator = "\\" if spec.target_platform is Platform.WINDOWS else "/"
    modules = oracle.canonicalize(launcher.pshome.rstrip("\\/") + separator + "Modules")  # type: ignore[union-attr]
    if modules is None:
        return ""
    if not trusted_root_chain(
        modules, spec.execution_subject, oracle, spec.target_platform  # type: ignore[arg-type]
    ):
        return ""
    return str(modules)


# ENV-06 (2): the two registered shapes, and the reserved keys no source can put back.
DESCRIPTIVE_KEYS: FrozenSet[str] = frozenset({
    "USERNAME", "USERDOMAIN", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "OS", "USER", "LOGNAME", "LANG", "LC_*", "TZ", "TERM", "COLUMNS", "LINES", "NO_COLOR",
})

PROXY_KEYS: FrozenSet[str] = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
})

RESERVED_KEYS: FrozenSet[str] = frozenset({
    "BASH_ENV", "ENV", "BASH_FUNC_*", "SHELLOPTS", "BASHOPTS", "PATH", "PATHEXT",
    "PSModulePath", "NoDefaultCurrentDirectoryInExePath", "ComSpec", "MSYS_NO_PATHCONV",
})

REMOVED_KEYS: FrozenSet[str] = frozenset({
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
})
"""ENV-06d, named for the record. Membership changes nothing — everything outside groups (1)
and (2) is dropped — but these are the keys the rule exists for, and a reader looking for
``XDG_CONFIG_HOME`` should find it rather than infer it from an absence."""


def fold_key(key: str, target: Platform) -> str:
    return key.upper() if target is Platform.WINDOWS else key


def fold_keys(keys: Iterable[str], target: Platform) -> FrozenSet[str]:
    return frozenset(fold_key(k, target) for k in keys)


def fold_base(base: Mapping[str, str], target: Platform) -> Dict[str, str]:
    """ENV-06e: fold first, then do set arithmetic; a folded collision with two values drops.

    On Windows ``Path`` and ``PATH`` are one key, and which value survives would otherwise be
    decided by dictionary order — an accident deciding what the child inherits.
    """
    folded: Dict[str, str] = {}
    dropped: set = set()
    for key, value in base.items():
        name = fold_key(key, target)
        if name in dropped:
            continue
        if name in folded and folded[name] != value:
            del folded[name]
            dropped.add(name)
            continue
        folded[name] = value
    return folded


def matches_any(key: str, patterns: FrozenSet[str]) -> bool:
    return any((p.endswith("*") and key.startswith(p[:-1])) or key == p for p in patterns)


_PATHISH = re.compile(r"[\\/]")


def value_ok(key: str, value: str, spec: ShellSpec, inputs: EnvInputs) -> bool:
    """ENV-06 (2): the shape this key is registered for, and nothing else's shape.

    A value that fails is removed, never rewritten: an environment agentao edited into shape
    is one nobody wrote and nobody can predict.

    Three shapes, not one shape plus qualifiers. The descriptive keys are a single token with
    no path separator, so "contains no relative path" is already true of everything that
    passes. The proxy keys are URLs and host lists, where those clauses are not merely
    redundant but wrong — ``http://proxy:8080`` contains ``//`` and ``.internal`` starts with
    a dot, and neither is a path. The clauses about relative paths and the work tree belong to
    the third group: keys a user granted by name (ENV-06d), whose values *are* paths and which
    have no registered shape of their own.
    """
    if "\x00" in value or has_lone_surrogate(value):
        return False
    target = spec.target_platform
    folded = fold_key(key, target)
    if matches_any(folded, fold_keys(DESCRIPTIVE_KEYS, target)):
        return bool(value) and not any(ch.isspace() for ch in value) and not _PATHISH.search(value)
    if matches_any(folded, fold_keys(PROXY_KEYS, target)):
        if not value or any(ch.isspace() for ch in value):
            return False
        if matches_any(folded, fold_keys({"NO_PROXY", "no_proxy"}, target)):
            return True  # a host list, not a URL
        return "://" in value
    separator = ";" if target is Platform.WINDOWS else ":"
    for part in value.split(separator):
        if not part:
            continue
        if _is_absolute(part, target):
            if path_within(AbsPath(part), inputs.cwd, target) or path_within(
                AbsPath(part), inputs.project_root, target
            ):
                return False  # a value pointing into the work tree is a value the subject writes
        elif _PATHISH.search(part) or part in (".", ".."):
            return False  # a relative path: what it resolves against is not fixed
    return True


def child_env(
    spec: ShellSpec, pinned: PinnedEnv, inputs: EnvInputs, search_path: Tuple[AbsPath, ...]
) -> FrozenEnv:
    """ENV-06: the child's whole environment — a closed set in three groups.

    The dividing question is "is this key's value a path", because the effect table measures
    the command line only (EFF-01) and ``GIT_CONFIG_GLOBAL``, ``NODE_OPTIONS=--require``,
    ``PYTHONPATH`` and ``LD_PRELOAD`` are how a command-line-inert trusted program is handed
    code to run. Handing over the *configuration root* does the same: point
    ``XDG_CONFIG_HOME`` at somewhere the subject writes and ``git status`` reads its config
    from there — a path outside the work tree, which no work-tree check can see.
    """
    target = spec.target_platform
    env: Dict[str, str] = {}
    # (1) Pinned. Values come from ``spec.pinned_env``, never copied from the base
    #     environment; a field left None is a key that does not appear.
    env["PATH"] = join_path(search_path, target)  # ENV-01a: the sequence decide() already computed
    env["PATHEXT"] = ".COM;.EXE"  # ENV-02, on every rung
    pinned_fields: Tuple[Tuple[str, Optional[str]], ...] = (
        ("SystemRoot", pinned.system_root),
        ("windir", pinned.windir),
        ("SystemDrive", pinned.system_drive),
        ("ProgramData", pinned.program_data),
        ("ProgramFiles", pinned.program_files),
        ("ProgramFiles(x86)", pinned.program_files_x86),
        ("ProgramW6432", pinned.program_w6432),
        ("CommonProgramFiles", pinned.common_program_files),
        ("CommonProgramFiles(x86)", pinned.common_program_files_x86),
        ("ALLUSERSPROFILE", pinned.all_users_profile),
        ("PUBLIC", pinned.public),
        ("ComSpec", pinned.com_spec),
        ("HOME", pinned.home),
        ("USERPROFILE", pinned.user_profile),
        ("HOMEDRIVE", pinned.home_drive),
        ("HOMEPATH", pinned.home_path),
        ("APPDATA", pinned.appdata),
        ("LOCALAPPDATA", pinned.local_appdata),
        ("TEMP", pinned.temp),
        ("TMP", pinned.tmp),
        ("TMPDIR", pinned.tmpdir),
    )
    for key, value in pinned_fields:
        if value is not None:
            env[key] = str(value)
    if spec.rung is Rung.cmd:
        env["NoDefaultCurrentDirectoryInExePath"] = "1"  # ENV-04
    if spec.rung in POWERSHELL_RUNGS:
        env["PSModulePath"] = pinned_psmodulepath(spec)  # ENV-05
    if spec.rung is Rung.git_bash:
        env["MSYS_NO_PATHCONV"] = "1"  # LAUNCH-04
    pinned_keys = fold_keys(env, target)
    # (2) Passed through, value-checked. ENV-06d grants literal key names only: an entry
    #     containing ``*`` is dropped, because one ``*`` puts the whole inherited environment
    #     back — the very chain this rule closes.
    granted = frozenset(k for k in spec.env_passthrough if "*" not in k)
    keep = fold_keys(DESCRIPTIVE_KEYS | PROXY_KEYS | granted, target)
    reserved = fold_keys(RESERVED_KEYS, target)
    for key, value in fold_base(inputs.base, target).items():
        if matches_any(key, reserved) or key in pinned_keys:
            continue
        if matches_any(key, keep) and value_ok(key, value, spec, inputs):
            env[key] = value
    # (3) Removed: every key that hands "where to read configuration" or "what to trust" to
    #     the environment, and everything not in (1) or (2). There is no branch for it —
    #     falling out of the loop above *is* the removal.
    return MappingProxyType(dict(env))


# ------------------------------------------------------------------ LAUNCH-08


CREATEPROCESS_MAX_UNITS = 32767
"""LAUNCH-08 (i): including the terminating NUL, so the text itself may be 32766."""

CMD_MAX_CHARS = 8191
"""LAUNCH-08 (ii): cmd's own limit on the command-line text, and on each inherited
``KEY=VALUE`` it will otherwise drop one at a time."""

POINTER_BYTES = 8
"""LAUNCH-08 (iii): one pointer per argv / envp entry. Inferred for a 64-bit target rather
than measured — the POSIX measurement is unreachable end-to-end today (G18-12), because every
policy-on rung targets Windows."""


def has_lone_surrogate(text: str) -> bool:
    r"""LAUNCH-08e: an unpaired ``U+D800``-``U+DFFF``, which no encoding can represent.

    A tool argument arrives as JSON and a ``\ud800`` escape decodes into the Python string
    verbatim. All three measurements encode first, so without this check the floor raises
    ``UnicodeEncodeError`` *before any analysis* — and an exception is not a verdict: it
    bypasses the reason vocabulary, bypasses TOOL-03's "the floor's DENY cannot be masked by a
    rule", and over ACP can reach the model as a tool error worth retrying.
    """
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def createprocess_units(command_line: str) -> int:
    """LAUNCH-08 (i): UTF-16 code units of what reaches ``CreateProcessW``, plus the NUL."""
    return len(command_line.encode("utf-16-le", errors="surrogatepass")) // 2 + 1


def cmd_line_chars(command_line: str) -> int:
    """LAUNCH-08 (ii): the command-line text's own length, without a terminator."""
    return len(command_line.encode("utf-16-le", errors="surrogatepass")) // 2


def bytes_with_nul(text: str) -> int:
    """LAUNCH-08 (iii): the target encoding's byte count for one entry, including its NUL."""
    return len(text.encode("utf-8", errors="surrogateescape")) + 1


def execve_total_units(request: PosixLaunch) -> int:
    """LAUNCH-08 (iii): argv and envp together, each entry with its NUL and its pointer."""
    strings = [*request.argv, *(f"{k}={v}" for k, v in request.env.items())]
    return sum(bytes_with_nul(s) for s in strings) + POINTER_BYTES * len(strings)


@dataclass(frozen=True)
class PosixLimits:
    """LAUNCH-08 (iii): looked up at runtime, never written down — the page size decides."""

    arg_max: int
    max_arg_strlen: int


def posix_limits(spec: ShellSpec) -> PosixLimits:
    """This host's ``ARG_MAX`` and ``PAGE_SIZE * 32``.

    Only ever this host's: every policy-on rung targets Windows (``derive_rung`` maps a POSIX
    target to ``system_posix``, which is policy-off), so a POSIX target never reaches the
    measurement in production. That is why the contract leaves "which oracle method answers
    for a remote POSIX target" open — there is nothing to answer for yet. G18-12 drives these
    functions directly with a stubbed ``sysconf`` and says so.
    """
    del spec  # the target's own limits need an interface that q4 leaves open (LAUNCH-08)
    try:
        arg_max = int(os.sysconf("SC_ARG_MAX"))
    except (AttributeError, ValueError, OSError):  # pragma: no cover - not a POSIX host
        arg_max = 0
    try:
        page_size = int(os.sysconf("SC_PAGESIZE"))
    except (AttributeError, ValueError, OSError):  # pragma: no cover - not a POSIX host
        page_size = 4096
    return PosixLimits(arg_max=arg_max, max_arg_strlen=page_size * 32)


def command_line_of(request: AttestedLaunch) -> str:
    """What Windows actually receives: a list form is serialised by ``list2cmdline`` first."""
    if isinstance(request, WindowsLaunch):
        return request.command_line
    return subprocess.list2cmdline(list(request.argv))


def oversize_reason(spec: ShellSpec, request: AttestedLaunch) -> Optional[str]:
    """LAUNCH-08: the refusal an over-long launch earns, measured in the target's own units.

    Three measurements, each covering its own thing and never summed. Truncation is not an
    option anywhere: cut inside cmd's ``/s`` quoting and the structure the floor analysed
    stops being the structure cmd runs.
    """
    if not spec.policy_enabled:
        return None  # LAUNCH-08 constrains policy-on rungs only (LADDER-05)
    if spec.target_platform is Platform.WINDOWS:
        line = command_line_of(request)
        if createprocess_units(line) > CREATEPROCESS_MAX_UNITS:
            return "launch-oversize"
        if spec.rung is Rung.cmd:
            if cmd_line_chars(line) > CMD_MAX_CHARS:
                return "launch-oversize"
            for key, value in request.env.items():
                if cmd_line_chars(f"{key}={value}") > CMD_MAX_CHARS:
                    return "launch-env-oversize"  # cmd drops these one at a time; we refuse
        return None
    if not isinstance(request, PosixLaunch):  # pragma: no cover - the union has two members
        return "launch-oversize"
    limits = posix_limits(spec)
    for argument in request.argv:
        if bytes_with_nul(argument) > limits.max_arg_strlen:
            return "launch-oversize"
    for key, value in request.env.items():
        if bytes_with_nul(f"{key}={value}") > limits.max_arg_strlen:
            return "launch-env-oversize"  # the reason follows whose entry it was (LAUNCH-08c)
    if limits.arg_max and execve_total_units(request) > limits.arg_max:
        return "launch-oversize"
    return None


# ------------------------------------------------------------------ LAUNCH-02..09


PS_SINGLE_QUOTES = "‘’‚‛"
"""LAUNCH-09e: PowerShell's lexer treats these four as single-quote delimiters alongside the
ASCII one, and whether they can be escaped by doubling is unmeasured."""

CMD_FORBIDDEN_IN_WORKDIR = '"%^&|<>\r\n'
"""LAUNCH-09b. The two newlines are the important half: one CR or LF inside a ``/c`` string
cuts the command line, and the text after it runs as another command — outside ``/s``'s outer
quotes, and outside the structure the floor analysed."""


def encode_workdir(cwd: AbsPath, dialect: ShellDialect) -> Optional[str]:
    """LAUNCH-09: ``<W>`` in the dialect's own literal rules, or ``None`` (``launch-cwd``).

    The quotes around it belong to LAUNCH-02..05's spelling; what comes back is the text that
    goes between them.
    """
    if "\x00" in cwd or has_lone_surrogate(cwd):
        return None
    if dialect is ShellDialect.POWERSHELL:
        if any(ch in cwd for ch in PS_SINGLE_QUOTES):
            return None  # LAUNCH-09e: refuse rather than guess whether doubling escapes them
        return cwd.replace("'", "''")
    if dialect is ShellDialect.POSIX:
        return cwd.replace("'", "'\\''")
    if dialect is ShellDialect.CMD:
        return None if any(ch in cwd for ch in CMD_FORBIDDEN_IN_WORKDIR) else cwd
    return None


def _ps_literal(text: str) -> Optional[str]:
    """A PowerShell single-quoted literal's body, or ``None`` when it cannot be encoded.

    Same rule as ``<W>`` (LAUNCH-09e) and the same reason: one of the four typographic single
    quotes would close the literal and splice what follows into the *prelude*, which the floor
    never scans.
    """
    if "\x00" in text or has_lone_surrogate(text) or any(c in text for c in PS_SINGLE_QUOTES):
        return None
    return text.replace("'", "''")


def prelude_for(identity: InterpreterIdentity, workdir_literal: str) -> Optional[str]:
    """LAUNCH-05: the guard, then the change of directory, then (by the caller) the body.

    Byte-fixed on purpose. It is the back half of the same ``-Command`` argument, so no byte
    of the body can run ahead of it — and the guard runs before the directory change, so a
    failed guard has not touched the work tree.

    ``<C-check>`` is omitted only in the case LAUNCH-06 allows: preflight found no console
    session configuration in any of the three sources, which ``attested_spec`` has already
    made the only reachable case by refusing every rung where one exists. Returning ``None``
    covers the other reading, where a configuration exists and no expression reports it.

    Takes the encoded working directory because ``<W>`` is per call while the identity is per
    rung; the contract's one-argument signature has nowhere to put it (LAUNCH-05, LAUNCH-09a).
    """
    if identity.session_config is not None:
        return None  # LAUNCH-06: <C> may not be quietly dropped, and $PSHOME cannot stand in
    edition = _ps_literal(identity.edition)
    version = _ps_literal(identity.version)
    pshome = _ps_literal(str(identity.pshome))
    if edition is None or version is None or pshome is None or not identity.pshome:
        return None  # LAUNCH-05: unencodable identity fields refuse the rung, never re-escape
    # 1. Identity, using only what ``Microsoft.PowerShell.Core`` and .NET provide. The first
    #    version of this line read ``$PSHOME`` through ``Get-Item``, which is a
    #    ``Microsoft.PowerShell.Management`` cmdlet — and step 3 below is what makes that
    #    module unavailable. Measured on windows-latest: under
    #    ``$PSModuleAutoLoadingPreference='None'`` in ``pwsh -NoProfile -Command``, none of
    #    ``Get-Item``, ``Set-Location``, ``Write-Output``, ``Get-Date`` or ``Get-ChildItem``
    #    resolves. A ``[System.IO.Path]`` static always does.
    guard = (
        f"if ($PSVersionTable.PSEdition -ne '{edition}'"
        f" -or $PSVersionTable.PSVersion.ToString() -ne '{version}'"
        f" -or [System.IO.Path]::GetFullPath($PSHOME) -ne '{pshome}'"
        ") { exit 97 }"
    )
    # 2. Load the two modules the trusted table is written against, **after** the identity
    #    check and before the door closes. ENV-05 calls the pinned ``PSModulePath`` the
    #    mechanism and auto-loading-off the depth; this is what makes both true at once —
    #    these two come from the install root just verified, and nothing else can arrive
    #    implicitly afterwards. Failing to load them is an unattested startup state, not a
    #    working directory problem, so it exits 97 with the rest of the guard.
    load = (
        "try { Import-Module -Name Microsoft.PowerShell.Management, "
        "Microsoft.PowerShell.Utility -ErrorAction Stop } catch { exit 97 }"
    )
    # 3. Close the door, then check it closed — a session configuration can set it back.
    pin = (
        "$PSModuleAutoLoadingPreference='None'; "
        "if ($PSModuleAutoLoadingPreference -ne 'None') { exit 97 }"
    )
    move = (
        f"try {{ Set-Location -LiteralPath '{workdir_literal}' -ErrorAction Stop }} "
        "catch { exit 98 }"
    )
    return f"{guard}; {load}; {pin}; {move}"


def _parent_dir(path: AbsPath, target: Platform) -> Optional[AbsPath]:
    ancestors = ancestors_to_volume_root(path, target)
    return ancestors[0] if ancestors else None


def request_for(
    spec: ShellSpec,
    launcher: LauncherIdentity,
    body: str,
    workdir_literal: str,
    env: FrozenEnv,
    cwd: AbsPath,
    attested_images: Tuple[ResolvedImage, ...],
) -> Optional[AttestedLaunch]:
    """LAUNCH-02 / 03 / 04: this rung's command line, assembled once.

    ``cwd`` on the request is the launcher's own directory (LAUNCH-09), never the call's
    working directory — Windows searches the current directory for DLLs before ``PATH``, and
    starting in the work tree would run a planted dependency before the prelude exists.
    ``workdir`` carries the call's working directory as a canonical path; the dialect-encoded
    ``<W>`` appears only inside the command line.

    The variants are shapes, not platforms: a PowerShell rung on Windows launches through the
    argv form (``PosixLaunch``) because LAUNCH-02 says ``Popen(list, shell=False)``, and the
    cmd rung through the single-string form because ``/s`` needs the outer quotes intact.
    """
    launcher_dir = _parent_dir(launcher.path, spec.target_platform)
    if launcher_dir is None:
        return None
    shared = dict(
        workdir=cwd,
        env=env,
        execution_subject=spec.execution_subject,
        attested_images=attested_images,
        spec_fingerprint=spec.fingerprint,
        cwd=launcher_dir,
    )
    path = str(launcher.path)
    if spec.rung in POWERSHELL_RUNGS:
        if not isinstance(launcher, InterpreterIdentity):
            return None  # IMG-07: a PowerShell rung without a measured identity has no prelude
        prelude = prelude_for(launcher, workdir_literal)
        if prelude is None:
            return None
        # LAUNCH-02: the prelude and the body are ONE element. Splitting them hands PowerShell
        # two arguments it will rejoin by its own rules rather than by the floor's.
        return PosixLaunch(
            executable=AbsPath(path),
            argv=(path, "-NoProfile", "-NonInteractive", "-Command", f"{prelude}; {body}"),
            **shared,
        )
    if spec.rung is Rung.cmd:
        inner = f'cd /d "{workdir_literal}" || exit 98 & {body}'
        return WindowsLaunch(
            application_name=AbsPath(path),
            command_line=f'"{path}" /d /e:on /v:off /s /c "{inner}"',
            **shared,
        )
    if spec.rung is Rung.git_bash:
        inner = f"cd -P -- '{workdir_literal}' || exit 98; {body}"
        return PosixLaunch(
            executable=AbsPath(path),
            argv=(path, "--noprofile", "--norc", "-p", "-c", inner),
            **shared,
        )
    return None  # the two policy-off rungs launch through LegacyLaunch (LAUNCH-01c)


# ------------------------------------------------------------------ NAME-02's table


@dataclass(frozen=True)
class MeasuredEntry:
    """One row of the ``Get-Command -All`` table measured for a single interpreter identity."""

    name: str
    kind: str  # "alias" | "function" | "cmdlet"
    alias_target: Optional[str] = None


MEASURED_COMMAND_TABLES: Dict[Tuple[str, str], Tuple[MeasuredEntry, ...]] = {}
"""NAME-02: alias / function / cmdlet rows, keyed by ``(edition, version)``.

Empty, and that is the honest state: the rule says the table is measured **in the pinned
startup state** this PR builds — ``-NoProfile``, auto-loading off, the default session
configuration — which can only happen on Windows. G21 in the gates document is where it gets
filled. Until then :func:`identity_measured` answers ``False`` for every identity and NAME-02
fails closed: every PowerShell bare word is opaque and the rung still serves explicit paths
(IMG-04), exactly as it does when the closed environment is not established.

A table measured with auto-loading on would allow words the child then fails to find, and a
table shared across editions either trusts a name that is not there or misses one that is.
"""


def identity_measured(identity: LauncherIdentity) -> bool:
    """IMG-07: whether NAME-02's command table was measured for this interpreter identity.

    This is not the edition table :func:`derive_rung` reads. Both are called "the measured
    table" in the specification and they answer different questions: the edition table decides
    *which rung this is*, and a miss there refuses the source (CFG-02a); this one decides
    *whether bare words can be resolved*, and a miss makes every one of them opaque while the
    rung itself stands (IMG-07, NAME-02).
    """
    if not isinstance(identity, InterpreterIdentity):
        return False
    return (identity.edition, identity.version) in MEASURED_COMMAND_TABLES


def measured_table(identity: LauncherIdentity) -> Tuple[MeasuredEntry, ...]:
    if not isinstance(identity, InterpreterIdentity):
        return ()
    return MEASURED_COMMAND_TABLES.get((identity.edition, identity.version), ())


# ------------------------------------------------------------------ LADDER-01..03


def powershell_parser_available() -> bool:
    """LADDER-01: a missing grammar makes the PowerShell rungs unselectable, not degraded."""
    from ._powershell import parser_available

    return parser_available()


def derive_rung(
    dialect: ShellDialect, target: Platform, identity: LauncherIdentity
) -> Optional[Rung]:
    """CFG-02a's fixed table: (dialect, target platform, image identity) -> rung.

    The target platform is read on every row, never the host's. A ``cmd`` dialect on a POSIX
    target derives nothing: ``cmd.exe`` does not exist there, and the policy-off ``legacy_cmd``
    is pre-flip *Windows*'s default rather than a POSIX rung. Deriving a policy-on ``cmd`` spec
    instead would leave no measurement that applies to it, so every call would be refused for
    being over-long — a platform error reported as a length.
    """
    if dialect is ShellDialect.CMD:
        return Rung.cmd if target is Platform.WINDOWS else None
    if dialect is ShellDialect.POSIX:
        return Rung.git_bash if target is Platform.WINDOWS else Rung.system_posix
    if dialect is ShellDialect.POWERSHELL and isinstance(identity, InterpreterIdentity):
        if identity.edition == "Core":
            return Rung.pwsh
        if identity.edition == "Desktop":
            return Rung.powershell
    return None  # UNKNOWN dialect, no readable edition, or an edition nobody has measured


def attested_spec(
    rung: Rung,
    img: ResolvedImage,
    identity: LauncherIdentity,
    config: ShellBlock,
    oracle: IdentityOracle,
    target: Platform,
    subject: Subject,
    local: bool,
) -> Union[ShellSpec, Exhausted]:
    """Every precondition first, then one frozen object (SPEC-07). Nothing is ever assigned.

    An unmeasured NAME-02 table is deliberately *not* a reason to refuse here. IMG-07 says
    that case makes the rung's bare words opaque, and NAME-02 already spells out the identical
    degradation for its sibling condition — the closed environment not being established — as
    "every PowerShell bare word is opaque, the rung still serves explicit paths through
    IMG-04". Refusing the rung instead would drop Windows to ``cmd``, whose floor is coarser,
    over a missing *name* table.
    """
    if not trusted_image(img, subject, config.allowlist, oracle, target):
        return Exhausted("IMG-02: launcher image")
    pinned = oracle.target_pinned_env(subject)  # ENV-06 (1)
    if pinned is None or pinned.has_unknown_keys or not pinned.shapes_ok(target):
        return Exhausted("ENV-06: pinned env")
    if any(
        not trusted_root_chain(p, subject, oracle, target) for p in pinned.system_paths()
    ):
        return Exhausted("ENV-06: pinned system dir")
    established = False
    if rung in POWERSHELL_RUNGS:
        if not powershell_parser_available():
            return Exhausted("no parser")  # LADDER-01
        if not isinstance(identity, InterpreterIdentity):
            return Exhausted("IMG-07: identity")
        pshome = oracle.resolve_pshome(img)
        if pshome is None:
            return Exhausted("IMG-08: no $PSHOME")  # never fall back to the launcher's directory
        if identity.pshome != pshome:
            return Exhausted("IMG-08: identity $PSHOME is not the resolved install root")
        session = oracle.read_config_sources(pshome, subject).session
        if session is not None:
            return Exhausted("IMG-08: session config")
        if identity.session_config != session:
            return Exhausted("IMG-08: identity session config is not the one read from disk")
        # Both of those align the two sources for ``<H>`` and ``<C>``. ``launch()`` re-reads
        # them before spawning and compares against the launcher's own fields (IMG-08a), so a
        # spec built without this check would either deny every time or have been validated
        # against an install root the re-read never looks at.
        # IMG-09's preflight runs the *same* prelude, so it needs a working directory too —
        # and an empty one would make its ``Set-Location`` fail and the child exit 98, which
        # reads back as "the closed environment was not established" on every healthy
        # interpreter. The launcher's own directory is the one LAUNCH-09 already starts in.
        home = _parent_dir(identity.path, target)
        literal = encode_workdir(home, ShellDialect.POWERSHELL) if home is not None else None
        prelude = prelude_for(identity, literal) if literal is not None else None
        established = prelude is not None and oracle.preflight(identity, prelude)
    draft = ShellSpec(
        dialect=dialect_of(rung),
        rung=rung,
        filesystem_is_local=local,  # SPEC-04a: written explicitly, never left to the default
        execution_subject=subject,
        identity_oracle=oracle,
        launcher=identity,
        pinned_env=pinned,
        env_passthrough=config.env_passthrough,
        allowlist=config.allowlist,
        explicit_shell=None,
        target_platform=target,
        policy_enabled=True,
        closed_env_established=established,
        fingerprint=Sha256(""),
    )
    spec = replace(draft, fingerprint=fingerprint_of(fingerprint_projection(draft)))
    bad = validate(spec)  # SPEC-02 / SPEC-03, at construction, naming what failed
    return Exhausted(bad) if bad is not None else spec


def select_rung(
    config: ShellBlock, oracle: IdentityOracle, subject: Subject
) -> Union[ShellSpec, Exhausted]:
    """CFG-02 and LADDER-01: which interpreter this host will run, decided once.

    Local and non-local both go through the oracle (SPEC-05), and the oracle is bound to this
    subject. The platform and the locality are each read exactly once at the top and passed
    down; re-asking inside the loop would let a spec be derived for one platform and launched
    measuring another (G18-14).
    """
    if not oracle_answers(oracle, SELECTION_METHODS):
        return Exhausted("SPEC-05c: oracle cannot answer the target platform")
    target = oracle.target_platform()
    local = target_is_local(oracle)  # SPEC-04a
    if (config.path is None) != (config.dialect is None):
        return Exhausted("shell block names only one of path / dialect")  # CFG-02
    if config.path is not None and config.dialect is not None:
        # This source supplies a whole spec (CFG-02). Refused here means Exhausted, never a
        # quiet fall back to ``auto`` — LADDER-03 is explicit about that.
        if not oracle_complete(oracle):
            return Exhausted("SPEC-05c: incomplete oracle")
        img = oracle.resolve_image(config.path, subject)  # canonicalisation happens here
        if img is None or not trusted_root_chain(img.canonical_path, subject, oracle, target):
            return Exhausted("IMG-01: shell.path")  # IMG-05 (b): no signature, but position
        root = oracle.target_project_root()
        if root is None or path_within(img.canonical_path, root, target):
            # A trusted chain is not the same as an acceptable position: a repository-supplied
            # interpreter in a read-only checkout is unreplaceable by the subject, so the chain
            # answers "trusted" — and IMG-04 says the work tree is never a trusted root. Both
            # have to pass.
            return Exhausted("IMG-05a: shell.path inside the project root")
        identity = oracle.read_identity(img, config.dialect)  # IMG-07: takes the dialect
        if identity is None:
            return Exhausted("IMG-07: launcher")
        if identity.image is not img or img.execution_subject != subject:
            return Exhausted("IMG-07: identity does not bind this image/subject")
        rung = derive_rung(config.dialect, target, identity)
        if rung is None:
            return Exhausted("CFG-02: no rung for this dialect / platform / identity")
        if rung is Rung.git_bash and not GIT_BASH_RELEASED:
            return Exhausted("LADDER-04: git_bash rung not released")
        if rung in POLICY_OFF_RUNGS:
            # An explicit source can derive a policy-off rung too (a POSIX target named with
            # the posix dialect). Its launcher and pinned environment are None by SPEC-03, but
            # the executable the user named travels with it (CFG-02c): dropping it would
            # quietly downgrade "a high source supplies the whole spec" to "…all of it except
            # the interpreter".
            return legacy_spec(
                config.dialect, rung, target, subject, local, img.canonical_path
            )
        return attested_spec(rung, img, identity, config, oracle, target, subject, local)
    # A block carrying only allow_git_bash / allowlist / env_passthrough parameterises ``auto``
    # rather than replacing it, so the ladder still runs (LADDER-02).
    if target is Platform.POSIX:
        return legacy_spec(ShellDialect.POSIX, Rung.system_posix, target, subject, local)
    if not LADDER_FLIPPED:  # LADDER-05: pre-flip Windows reports CMD x legacy_cmd
        return legacy_spec(ShellDialect.CMD, Rung.legacy_cmd, target, subject, local)
    if not oracle_complete(oracle):
        return Exhausted("SPEC-05c: incomplete oracle")
    ladder = [
        Rung.pwsh,
        Rung.powershell,
        *([Rung.git_bash] if config.allow_git_bash and GIT_BASH_RELEASED else []),
        Rung.cmd,
    ]
    for rung in ladder:
        img = oracle.discover(rung, subject)  # IMG-05 (a); a PATH hit is not a candidate
        if img is None:
            continue
        if not trusted_root_chain(img.canonical_path, subject, oracle, target):
            continue
        if not host_identity_ok(img, config.allowlist, oracle):  # IMG-05
            continue
        identity = oracle.read_identity(img, dialect_of(rung))
        if identity is None:
            continue
        if identity.image is not img or img.execution_subject != subject:
            continue
        if derive_rung(dialect_of(rung), target, identity) != rung:
            continue  # a Desktop identity in the pwsh slot is the next rung, not this one
        spec = attested_spec(rung, img, identity, config, oracle, target, subject, local)
        if not isinstance(spec, Exhausted):
            return spec
    return Exhausted("every rung refused")  # LADDER-03


# ------------------------------------------------------------------ NAME-01..03


@dataclass(frozen=True)
class NameResolution:
    """What a command word resolved to: an entry, an image, or a reason it is opaque.

    All three matter to the caller. IMG-02 closes the runnable set with *two* independent
    conditions, and a resolution that answered only "which entry" would let the name half
    stand in for the image half — which is exactly how a ``git.exe`` copied into the work tree
    gets to run.
    """

    entry: Optional[object] = None  # a ``_effects.TrustedEntry``
    image: Optional[ResolvedImage] = None
    external_name: Optional[str] = None  # the name whose image the caller must attest
    opaque: Optional[str] = None


_EXECUTABLE_SUFFIXES = (".COM", ".EXE")
"""ENV-02 pins ``PATHEXT`` to these two, in this order, so this is the whole search."""


def resolve(
    name: str, spec: ShellSpec, oracle: IdentityOracle, search_path: Tuple[AbsPath, ...]
) -> Optional[ResolvedImage]:
    """An external program's image, found on the *given* search path and nowhere else.

    ``search_path`` is the sequence ``decide()`` already computed for the child's ``PATH``
    (ENV-01a), never one fetched here. Two searches are two answers: the image the decision
    attested and the image the child opens can differ if PATH changed, or if the filter
    depended on this call's working directory, and LAUNCH-01d's re-check only covers the
    direct target.
    """
    target = spec.target_platform
    separator = "\\" if target is Platform.WINDOWS else "/"
    if target is Platform.WINDOWS and spec.dialect is not ShellDialect.POSIX:
        # NAME-01 / NAME-02: cmd and PowerShell find external commands through PATHEXT, which
        # ENV-02 has pinned to ``.COM;.EXE``. A name that already carries one is used as is.
        if name.upper().endswith(_EXECUTABLE_SUFFIXES):
            candidates: Tuple[str, ...] = (name,)
        else:
            candidates = tuple(name + suffix for suffix in _EXECUTABLE_SUFFIXES)
    else:
        candidates = (name,)  # NAME-03: bash searches the exact filename, no extension rules
    for directory in search_path:
        for candidate in candidates:
            joined = str(directory).rstrip("\\/") + separator + candidate
            canonical = oracle.canonicalize(joined)
            if canonical is None or not oracle.resolves_on_target(canonical):
                continue
            found = oracle.resolve_image(canonical, spec.execution_subject)
            if found is not None:
                return found
    return None


def _table_entry(word: str, dialect: ShellDialect):
    from ._effects import lookup as table_lookup

    return table_lookup(word, dialect)


def _basename_for_table(word: str, target: Platform) -> str:
    """The name the trusted table is keyed by: a basename, without a Windows image suffix."""
    tail = word.replace("\\", "/").rsplit("/", 1)[-1]
    if target is Platform.WINDOWS and tail.upper().endswith(_EXECUTABLE_SUFFIXES):
        return tail[:-4]
    return tail


def resolve_name(
    word: str, spec: ShellSpec, oracle: IdentityOracle, search_path: Tuple[AbsPath, ...]
) -> NameResolution:
    """NAME-01 / NAME-02 / NAME-03: what this bare word is, in this dialect, on this rung.

    The three rules differ because the interpreters differ, and the differences are not
    cosmetic: cmd consults an internal-command table and then ``PATHEXT``; PowerShell resolves
    alias before function before cmdlet before external program; bash has already resolved
    three classes of rebinding away before it ever searches ``PATH``.
    """
    from ._effects import EntryKind

    dialect = spec.dialect
    if dialect is ShellDialect.CMD:  # NAME-01
        entry = _table_entry(word, dialect)
        if entry is not None and entry.kind is EntryKind.internal:
            return NameResolution(entry=entry)
        return _external(word, spec, oracle, search_path)
    if dialect is ShellDialect.POWERSHELL:  # NAME-02
        if not spec.closed_env_established:
            # NAME-02's own condition: without the pinned startup state the table describes
            # some other interpreter's session. The rung still serves explicit paths (IMG-04).
            return NameResolution(opaque="closed-env-not-established")
        if spec.launcher is None or not identity_measured(spec.launcher):
            # IMG-07's sibling condition, and it degrades the same way NAME-02 already
            # degrades for the one above: bare words opaque, explicit paths still served.
            return NameResolution(opaque="identity-not-measured")
        return _powershell_name(word, spec, oracle, search_path)
    if dialect is ShellDialect.POSIX:  # NAME-03
        entry = _table_entry(word, dialect)
        if entry is not None and entry.kind in (EntryKind.builtin, EntryKind.keyword):
            # A word bash resolves before searching PATH is opaque unless this rung registers
            # it as inert: it never reaches a file, so there is no image to attest. Inert
            # means "registered with no trigger at all", not "no trigger fires for these
            # arguments" — ``eval`` with an empty argument list fires nothing and is still the
            # evaluator, and the question here is what the word *is*.
            if entry.execution_triggers or entry.rebind_triggers:
                return NameResolution(opaque="resolved-before-path-search")
            return NameResolution(entry=entry)
        return _external(word, spec, oracle, search_path)
    return NameResolution(opaque="unknown-dialect")


def _external(
    word: str, spec: ShellSpec, oracle: IdentityOracle, search_path: Tuple[AbsPath, ...]
) -> NameResolution:
    """The half of every rule that ends in "search the filtered PATH, then classify it".

    Both halves of IMG-02 are answered here and neither substitutes for the other: a name with
    no image is a ``git.exe`` copied into the work tree, and an image with no name is a
    program in a trusted directory that nobody has classified.
    """
    found = resolve(word, spec, oracle, search_path)
    if found is None:
        return NameResolution(opaque="not-found")
    entry = _table_entry(_basename_for_table(word, spec.target_platform), spec.dialect)
    if entry is None:
        return NameResolution(image=found, opaque="name")
    return NameResolution(entry=entry, image=found, external_name=word)


def _powershell_name(
    word: str, spec: ShellSpec, oracle: IdentityOracle, search_path: Tuple[AbsPath, ...]
) -> NameResolution:
    """NAME-02: alias, then function, then cmdlet, then external program — PowerShell's order.

    A function shadowing a same-named cmdlet or external program *is* the entry, and it needs
    its own EFF-08 registration; without one the word is opaque rather than falling through to
    whatever it shadowed. ``mkdir``, ``more`` and ``help`` are functions under ``-NoProfile``,
    so this order is not a corner case.
    """
    from ._effects import EntryKind

    folded = word.lower()
    rows = {row.name.lower(): row for row in measured_table(spec.launcher)}
    row = rows.get(folded)
    if row is not None and row.kind == "alias":
        target = (row.alias_target or "").strip()
        if not target:
            return NameResolution(opaque="alias-without-target")
        inner = rows.get(target.lower())
        if inner is None:
            # An alias onto an external program: the reason belongs to the *target's* image,
            # not to the alias's own name, or a trusted alias name would launder its target.
            return _external(target, spec, oracle, search_path)
        row = inner
        folded = target.lower()
    if row is not None:
        entry = _table_entry(folded, ShellDialect.POWERSHELL)
        if entry is None or entry.kind not in (EntryKind.cmdlet, EntryKind.function):
            return NameResolution(opaque=f"unregistered-{row.kind}")
        return NameResolution(entry=entry)
    return _external(word, spec, oracle, search_path)
