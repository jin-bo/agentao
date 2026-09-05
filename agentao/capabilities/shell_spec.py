"""The shell dialect vocabulary and the launch request agentao hands an executor.

This module is the runtime half of §3 of ``docs/design/powershell-support-spec.zh.md``;
``docs/design/powershell-support-contracts.py`` is the typed contract it is transcribed
from, and every rule ID below (``SPEC-01``, ``LAUNCH-01`` …) is defined there exactly once.

**What this module is for.** Today a shell command is a string that reaches
``subprocess.Popen(shell=True)``, so what actually interprets it is whatever the platform
picks — ``%COMSPEC% /c`` on Windows, ``/bin/bash`` or ``/bin/sh`` elsewhere. The floor that
inspects that string is written for POSIX shell syntax. On Windows that mismatch means the
floor is scanning cmd syntax with POSIX patterns, which is why its Windows token hit rate is
zero. Naming the dialect is the first step out of that, and the dialect has to travel *with*
the decision rather than be re-derived at spawn time.

**PR-1 changes shapes, not behaviour.** Every rung this module can construct today is
policy-off (``legacy_cmd`` on Windows, ``system_posix`` elsewhere, see ``LADDER-05``), and a
policy-off rung launches through :class:`LegacyLaunch`, which is field-for-field what
``ShellRequest`` carried before: the command string, the call's working directory, and
``build_child_env()``'s inherited-minus-credentials environment. The attested variants exist
and are enforced, but nothing constructs one until the trusted-resolution PR.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Mapping, NewType, Optional, Protocol, Tuple, Union, runtime_checkable

AbsPath = NewType("AbsPath", str)  # canonical absolute path, in the target's own path rules
AbsDir = NewType("AbsDir", str)
AbsFile = NewType("AbsFile", str)
DriveSpec = NewType("DriveSpec", str)  # ``C:`` — a drive, not a directory (SystemDrive, HOMEDRIVE)
RootRelPath = NewType("RootRelPath", str)  # ``\\Users\\x`` — root-relative, not absolute (HOMEPATH)
Sha256 = NewType("Sha256", str)
FsId = NewType("FsId", str)  # filesystem identity of the path the floor stat'd (SPEC-04)
Subject = NewType("Subject", str)  # the token the child will run as; the subject of IMG-01
EnvKey = NewType("EnvKey", str)  # a literal key name, never a pattern (ENV-06d)

FrozenEnv = Mapping[str, str]  # the frozen child environment (SPEC-07); a MappingProxyType at runtime


class ShellDialect(Enum):
    """SPEC-01. ``UNKNOWN`` is the value a host executor arrives with when it names none."""

    POSIX = "posix"
    POWERSHELL = "powershell"
    CMD = "cmd"
    UNKNOWN = "unknown"


class Rung(Enum):
    """SPEC-02. Which interpreter was selected, not merely which syntax it speaks."""

    pwsh = "pwsh"
    powershell = "powershell"
    cmd = "cmd"
    legacy_cmd = "legacy_cmd"  # pre-flip only (LADDER-05); PR-7 deletes this value
    git_bash = "git_bash"
    system_posix = "system_posix"


class Platform(Enum):
    WINDOWS = "windows"
    POSIX = "posix"


LEGAL_PAIRS: Mapping[ShellDialect, frozenset] = MappingProxyType(
    {
        ShellDialect.POWERSHELL: frozenset({Rung.pwsh, Rung.powershell}),
        ShellDialect.CMD: frozenset({Rung.cmd, Rung.legacy_cmd}),
        ShellDialect.POSIX: frozenset({Rung.git_bash, Rung.system_posix}),
    }
)
POLICY_OFF_RUNGS: frozenset = frozenset({Rung.system_posix, Rung.legacy_cmd})  # SPEC-03
POWERSHELL_RUNGS: frozenset = frozenset({Rung.pwsh, Rung.powershell})

# LADDER-04: the Git Bash rung ships behind its own switch, and only if its gate is green.
# Its reader is the ladder itself, which arrives with trusted resolution — nothing constructs
# a git_bash rung yet, so this cannot mislead in the meantime.
GIT_BASH_RELEASED = False
# LADDER-05: before the flip the ladder does not run at all and Windows reports legacy_cmd.
# `default_spec` reads this. It has to read *something*, because a constant that changes no
# behaviour when you flip it is worse than no constant: someone sets it to True to try the
# post-flip path, sees the pre-flip answer, and concludes the flip is already done.
LADDER_FLIPPED = False


def dialect_of(rung: Rung) -> ShellDialect:
    for dialect, rungs in LEGAL_PAIRS.items():
        if rung in rungs:
            return dialect
    return ShellDialect.UNKNOWN


# --------------------------------------------------------------- verdicts


@dataclass(frozen=True)
class Deny:
    """TOOL-03: DENY is the floor's only verdict, and no permission rule can mask it."""

    reason: str


@dataclass(frozen=True)
class Pass:
    """Not a decision to run — only the floor declining to refuse, which hands off to the rules."""


Verdict = Union[Deny, Pass]
PASS = Pass()


def opaque(dialect: ShellDialect, rule: str, detail: Optional[str] = None) -> Deny:
    """``hardline:<dialect>-opaque:<reason>`` — the floor's reason vocabulary (spec §3)."""
    return Deny(f"hardline:{dialect.value}-opaque:{detail or rule}")


@dataclass(frozen=True)
class Exhausted:
    """LADDER-03: every rung refused, or an explicit source was rejected.

    The tool stays registered and its provider exposes this instead of a spec, so the floor
    can answer ``hardline:no-trusted-rung-opaque`` before looking at any dialect or rung.
    Unregistering the tool would tell the model the capability does not exist, which is a
    different and worse lie than telling it this call was refused.
    """

    reason: str


# ------------------------------------------------------- images and identity


@dataclass(frozen=True)
class HashPin:
    """IMG-03 content pin: answers "this exact file was replaced", which a publisher cannot."""

    path: AbsPath
    sha256: Sha256

    def matches(self, img: "ResolvedImage") -> bool:
        return (
            img.canonical_path == self.path
            and isinstance(img.content_identity, HashPin)
            and img.content_identity.sha256 == self.sha256
        )


@dataclass(frozen=True)
class PublisherTrust:
    """IMG-03 publisher trust: attests the signer only, never which file."""

    signer: str


Allowlist = Tuple[Union[HashPin, PublisherTrust], ...]  # IMG-03; ordered, first match wins


@dataclass(frozen=True)
class ResolvedImage:
    """IMG-06: canonical path + filesystem identity + subject + content identity.

    Every site that takes an image takes this record, never a bare path — a path is a name
    for a file, and the whole question here is whether the file behind the name changed.
    """

    canonical_path: AbsPath
    filesystem_identity: FsId
    execution_subject: Subject
    content_identity: Optional[Union[HashPin, PublisherTrust]] = None


@dataclass(frozen=True)
class LauncherIdentity:
    """IMG-07: the attested image of the interpreter this rung launches, plus its hash."""

    image: ResolvedImage
    launcher_hash: Sha256  # recorded at construction, recomputed before spawn (launch-rehash)

    @property
    def path(self) -> AbsPath:
        return self.image.canonical_path


@dataclass(frozen=True)
class InterpreterIdentity(LauncherIdentity):
    """IMG-07 / IMG-08: the four extra facts a PowerShell rung reads from the image itself."""

    edition: str = ""
    version: str = ""
    pshome: AbsPath = AbsPath("")
    session_config: Optional[str] = None  # None = no console session configuration in any source


def is_abs_dir(path: str, target: Platform) -> bool:
    """ENV-06 (1): an absolute directory in the *target's* path rules, not the host's.

    A shape check on a POSIX host is asked about Windows values all the time — the target
    platform is a field on the spec precisely because the two answers differ.
    """
    if not path or "\x00" in path:
        return False
    if target is Platform.WINDOWS:
        if path.startswith("\\\\"):  # a UNC share root is absolute
            return len(path) > 2
        return len(path) >= 3 and path[0].isalpha() and path[1] == ":" and path[2] in "\\/"
    return path.startswith("/")


def is_abs_file(path: str, target: Platform) -> bool:
    """ENV-06 (1): ``ComSpec`` names a file, so a trailing separator disqualifies it.

    ``C:\\Windows\\System32\\`` is a directory however much it looks like a path, and an
    environment whose ``ComSpec`` names a directory is one nobody validated.
    """
    if not is_abs_dir(path, target):
        return False
    text = path.replace("\\", "/")
    if text.endswith("/"):
        return False
    tail = text.rsplit("/", 1)[-1]
    return bool(tail) and ":" not in tail


def is_drive_spec(value: str) -> bool:
    return len(value) == 2 and value[0].isalpha() and value[1] == ":"


def is_root_relative(value: str) -> bool:
    return value.startswith("\\") and not value.startswith("\\\\")


def drive_root(drive: DriveSpec) -> AbsPath:
    return AbsPath(drive + "\\")


@dataclass(frozen=True, kw_only=True)
class PinnedEnv:
    """ENV-06 (1): a closed set of named fields, deliberately not an arbitrary mapping.

    The fields divide by whether the value is a path the subject could point somewhere else.
    The system group is checked against IMG-01 before the spec is built; the profile group is
    writable by the subject by definition, so only its shape is checked — pinning them keeps
    the environment from *redirecting* a root, and was never going to keep the subject from
    writing inside one.
    """

    # System group — every one of these is checked against IMG-01. None off Windows.
    system_root: Optional[AbsDir] = None
    windir: Optional[AbsDir] = None
    system_drive: Optional[DriveSpec] = None
    program_data: Optional[AbsDir] = None
    program_files: Optional[AbsDir] = None
    program_files_x86: Optional[AbsDir] = None
    program_w6432: Optional[AbsDir] = None
    common_program_files: Optional[AbsDir] = None
    common_program_files_x86: Optional[AbsDir] = None
    all_users_profile: Optional[AbsDir] = None
    public: Optional[AbsDir] = None
    com_spec: Optional[AbsFile] = None  # ComSpec names a file, not a directory
    # Profile group — shape only, never IMG-01: the subject can write these by definition.
    home: AbsDir = AbsDir("")
    user_profile: Optional[AbsDir] = None
    home_drive: Optional[DriveSpec] = None
    home_path: Optional[RootRelPath] = None
    appdata: Optional[AbsDir] = None
    local_appdata: Optional[AbsDir] = None
    temp: AbsDir = AbsDir("")
    tmp: AbsDir = AbsDir("")
    tmpdir: Optional[AbsDir] = None  # POSIX target; None on a Windows target
    # Keys the oracle handed back that this table does not register (ENV-06b).
    unknown_keys: frozenset = frozenset()

    @property
    def has_unknown_keys(self) -> bool:
        return bool(self.unknown_keys)

    def shapes_ok(self, target: Platform) -> bool:
        """ENV-06f: every field's declared shape, *and* that this platform's fields are all here.

        The second half is the one that is easy to leave out. ``child_env`` renders a ``None``
        field as "this key does not appear", so an oracle that fails to answer ``SystemRoot``
        would silently hand the child an environment nobody validated — which is ENV-06a's
        reason, word for word.
        """
        windows_only: Tuple[Optional[str], ...] = (
            self.system_root, self.windir, self.system_drive, self.program_data,
            self.program_files, self.program_files_x86, self.program_w6432,
            self.common_program_files, self.common_program_files_x86, self.all_users_profile,
            self.public, self.com_spec, self.user_profile, self.home_drive, self.home_path,
            self.appdata, self.local_appdata,
        )
        if target is Platform.POSIX and any(v is not None for v in windows_only):
            return False
        if target is Platform.WINDOWS and self.tmpdir is not None:
            return False
        if target is Platform.WINDOWS:
            required: Tuple[Optional[str], ...] = (
                self.system_root, self.windir, self.system_drive, self.program_data,
                self.program_files, self.common_program_files, self.all_users_profile,
                self.public, self.com_spec, self.user_profile, self.home_drive, self.home_path,
                self.appdata, self.local_appdata,
            )
            # The three WOW64 keys are deliberately absent from that list: they are set by
            # WOW64 and do not exist at all on 32-bit Windows, so missing is a fact about the
            # platform rather than an oracle that could not answer (ENV-06f).
            if any(v is None for v in required):
                return False
        elif self.tmpdir is None:
            return False  # ENV-06f: a POSIX target answers TMPDIR or the rung is unattested
        # The three both platforms must answer. Declaring them non-Optional does not enforce
        # it: this record is built from an oracle's answers and a dataclass checks no
        # annotation at runtime, so a missing HOME would reach ``child_env`` as an empty one.
        if any(not v for v in (self.home, self.temp, self.tmp)):
            return False
        dirs = (
            self.system_root, self.windir, self.program_data, self.program_files,
            self.program_files_x86, self.program_w6432, self.common_program_files,
            self.common_program_files_x86, self.all_users_profile, self.public, self.home,
            self.user_profile, self.appdata, self.local_appdata, self.temp, self.tmp,
            self.tmpdir,
        )
        return (
            all(v is None or is_abs_dir(v, target) for v in dirs)
            and all(v is None or is_drive_spec(v) for v in (self.system_drive, self.home_drive))
            and (self.home_path is None or is_root_relative(self.home_path))
            and (self.com_spec is None or is_abs_file(self.com_spec, target))
        )

    def system_paths(self) -> Tuple[AbsPath, ...]:
        r"""The system group's paths, each checked against IMG-01 before the spec is built.

        ``public`` (``C:\Users\Public``) is **not** here (ENV-06g): it is a shared *user data*
        directory, writable by everyone by design, and no rule in this design loads or reads
        configuration from it. Leaving it in this group would make IMG-06a's ``FILE_ADD_FILE``
        clause refuse every policy-on rung — one make-weight environment key turning the whole
        ladder off, over a directory whose contents nothing reads.
        """
        paths = []
        for value in (
            self.system_root, self.windir, self.program_data, self.program_files,
            self.program_files_x86, self.program_w6432, self.common_program_files,
            self.common_program_files_x86, self.all_users_profile, self.com_spec,
        ):
            if value is not None:
                paths.append(AbsPath(value))
        if self.system_drive is not None:
            paths.append(drive_root(self.system_drive))
        return tuple(paths)


# ------------------------------------------------------------------ the spec


@dataclass(frozen=True, kw_only=True)
class ShellSpec:
    """SPEC-07: frozen, and frozen all the way down — every field is itself immutable.

    A shallow freeze would be no freeze at all. Reassigning ``spec.pinned_env.temp`` or
    appending to ``env_passthrough`` after construction changes the environment the child
    is launched with while the fingerprint stays equal, which is precisely the drift the
    fingerprint exists to detect.

    Re-resolution never mutates a spec; it builds a new one and the tool swaps the reference
    atomically (SPEC-07b), so a call that read the old reference keeps reading the old spec.
    """

    dialect: ShellDialect  # SPEC-01
    rung: Rung  # SPEC-02, checked against LEGAL_PAIRS at construction
    filesystem_is_local: bool = False  # SPEC-04; both constructors write the oracle's answer
    execution_subject: Subject
    identity_oracle: Optional[object] = None  # SPEC-05; a runtime object, so never fingerprinted
    closed_env_established: bool = False  # SPEC-06; false outside the PowerShell rungs
    launcher: Optional[LauncherIdentity] = None  # IMG-07; present iff policy is on (SPEC-03)
    pinned_env: Optional[PinnedEnv] = None  # ENV-06 (1); present iff policy is on
    env_passthrough: Tuple[EnvKey, ...] = ()  # ENV-06 (2), frozen at construction
    allowlist: Allowlist = ()  # IMG-03a: the one that was in force when the decision was made
    explicit_shell: Optional[AbsPath] = None  # CFG-02c; always None when policy is on
    target_platform: Platform  # a snapshot of oracle.target_platform(), never re-asked
    policy_enabled: bool  # SPEC-03; must equal ``rung not in POLICY_OFF_RUNGS``
    fingerprint: Sha256 = Sha256("")  # SPEC-08; the hash of fingerprint_projection()


def fingerprint_projection(spec: ShellSpec) -> Tuple[object, ...]:
    """SPEC-07's canonical projection: every field in declaration order except two.

    ``fingerprint`` itself is excluded because it is the output, and ``identity_oracle``
    because it is a live object with no canonical serialisation.
    """
    return (
        spec.dialect.value,
        spec.rung.value,
        spec.filesystem_is_local,
        spec.execution_subject,
        spec.closed_env_established,
        spec.launcher,
        spec.pinned_env,
        tuple(sorted(spec.env_passthrough)),
        spec.allowlist,
        spec.explicit_shell,
        spec.target_platform.value,
        spec.policy_enabled,
    )


def _encode(value: object) -> str:
    """A deterministic, injective-enough encoding of a projection element.

    Every part is length-prefixed and type-tagged so that no two distinct projections can
    encode to the same string by concatenation — ``("a", "bc")`` and ``("ab", "c")`` differ.
    Without the tags a ``None`` field and the string ``"None"`` would collide, which is
    exactly the case IMG-03a cares about: an allowlist entry present versus absent.
    """
    if value is None:
        return "N;"
    if isinstance(value, bool):  # before int/str: bool is an int subclass
        return f"B{int(value)};"
    if isinstance(value, str):
        return f"S{len(value)}:{value};"
    if isinstance(value, Enum):
        return f"E{_encode(value.value)}"
    if isinstance(value, (tuple, list)):
        return f"T{len(value)}:" + "".join(_encode(v) for v in value) + ";"
    if isinstance(value, (frozenset, set)):
        # Sorted, because a set has no order and two equal sets must encode identically —
        # an unsorted walk would make the fingerprint depend on insertion history.
        items = sorted(_encode(v) for v in value)
        return f"F{len(items)}:" + "".join(items) + ";"
    if is_dataclass(value) and not isinstance(value, type):
        parts = "".join(_encode(getattr(value, f.name)) for f in fields(value))
        return f"D{type(value).__name__}:{parts};"
    raise TypeError(f"no canonical encoding for {type(value).__name__} in a spec fingerprint")


def fingerprint_of(projection: Tuple[object, ...]) -> Sha256:
    """SPEC-07: the sha256 of the canonical projection."""
    return Sha256(hashlib.sha256(_encode(projection).encode("utf-8")).hexdigest())


def validate(spec: ShellSpec) -> Optional[str]:
    """SPEC-01 / SPEC-02 / SPEC-03, fail closed. Returns a floor reason, or None if legal.

    Run at construction *and* again when the floor is entered, on purpose: construction
    covers the specs agentao builds, and the second run covers a spec that reached the floor
    from a host executor without passing through a constructor here.
    """
    if spec.dialect not in LEGAL_PAIRS or spec.dialect is ShellDialect.UNKNOWN:
        return "hardline:unknown-dialect-opaque"  # SPEC-01
    if spec.rung not in LEGAL_PAIRS[spec.dialect]:
        return "hardline:unknown-rung-opaque"  # SPEC-02
    # SPEC-03's three cross-invariants. `policy_enabled` is not a field free to disagree
    # with the rung: a spec claiming policy while carrying no launcher would be checked by
    # rules that have nothing to check against.
    if spec.policy_enabled != (spec.rung not in POLICY_OFF_RUNGS):
        return "hardline:unknown-rung-opaque"
    if spec.policy_enabled and (spec.launcher is None or spec.pinned_env is None):
        return "hardline:unknown-rung-opaque"
    if not spec.policy_enabled and (spec.launcher is not None or spec.pinned_env is not None):
        return "hardline:unknown-rung-opaque"
    if spec.rung in POWERSHELL_RUNGS and not isinstance(spec.launcher, InterpreterIdentity):
        return "hardline:unknown-rung-opaque"  # IMG-07
    if spec.policy_enabled and spec.explicit_shell is not None:
        return "hardline:unknown-rung-opaque"  # CFG-02c: with policy on, the launcher decides
    return None


class SpecConstructionError(ValueError):
    """A spec that fails SPEC-01/02/03 is not built. The reason names the illegal pair."""


def _finalize(draft: ShellSpec) -> ShellSpec:
    """Validate, then stamp the fingerprint — one place, so the hash covers what is returned.

    ``replace`` rather than a second field-by-field literal: the projection reads every field
    but this one, and two field lists that can drift are two chances to fingerprint a spec
    that is not the one handed back.
    """
    reason = validate(draft)
    if reason is not None:
        raise SpecConstructionError(f"{reason} ({draft.dialect.value} x {draft.rung.value})")
    return replace(draft, fingerprint=fingerprint_of(fingerprint_projection(draft)))


def legacy_spec(
    dialect: ShellDialect,
    rung: Rung,
    target: Platform,
    subject: Subject,
    local: bool = False,
    explicit_shell: Optional[AbsPath] = None,
) -> ShellSpec:
    """A policy-off rung (LADDER-05, SPEC-03): no oracle, no pinned values, no identity read.

    There is nothing to attest at this rung, so ``allowlist`` and ``env_passthrough`` stay
    empty rather than carrying a copy nothing reads. ``explicit_shell`` is the one exception
    and it is not a copy: an explicit source has already passed the trust-root chain, the
    project-root check and the identity read, and only happens to derive a rung whose policy
    is off — dropping it would silently swap the interpreter the user named for today's.

    ``local`` is still written explicitly. It is a fact the executor declares, not something
    that follows from this rung having no policy.
    """
    return _finalize(
        ShellSpec(
            dialect=dialect,
            rung=rung,
            filesystem_is_local=local,
            execution_subject=subject,
            identity_oracle=None,
            launcher=None,
            pinned_env=None,
            env_passthrough=(),
            allowlist=(),
            explicit_shell=explicit_shell,
            target_platform=target,
            policy_enabled=False,
        )
    )


@dataclass(frozen=True, kw_only=True)
class ShellBlock:
    """CFG-01/CFG-02: the user-level or host-supplied shell configuration.

    Never workspace-level. That is a trust boundary rather than a filing decision: a rule
    checked into a repository must not be able to grant the agent a capability the person
    running it never approved.

    ``path`` and ``dialect`` are a pair. Giving one without the other is refused rather than
    guessed, because neither can be derived from the other: a renamed launcher tells you
    nothing about its syntax, and naming ``powershell`` does not say which edition and so
    does not settle which rung. Two sources each supplying half a spec is a configuration
    nobody can read back.
    """

    path: Optional[AbsPath] = None
    dialect: Optional[ShellDialect] = None
    allow_git_bash: bool = False  # LADDER-02; read before the last rung is chosen
    allowlist: Allowlist = ()
    env_passthrough: Tuple[EnvKey, ...] = ()

    def incomplete(self) -> Optional[str]:
        """CFG-02: the half that is missing, or ``None`` when the pair is consistent."""
        if (self.path is None) == (self.dialect is None):
            return None
        return "dialect" if self.dialect is None else "path"


def local_subject() -> Subject:
    """The token this machine's children run as — the subject IMG-01 asks its question about."""
    import getpass
    import os as _os

    try:
        return Subject(str(_os.geteuid()))  # POSIX: stable across a rename, unlike the login name
    except AttributeError:
        try:
            return Subject(getpass.getuser())
        except Exception:
            return Subject("")


def default_spec(windows: Optional[bool] = None, local: bool = False) -> ShellSpec:
    """The policy-off rung every platform reports until the ladder is turned on (LADDER-05).

    Windows answers ``CMD x legacy_cmd``: today's ``%COMSPEC% /c``, today's environment and
    today's floor, verdict for verdict identical to what shipped before any of this. Anywhere
    else it is ``POSIX x system_posix``, the shell that host already uses.

    Neither is a rung of the ladder. They are what ``auto`` means while the ladder is off,
    and without them a POSIX host and a pre-flip Windows host would both find the ladder
    empty — which LADDER-03 turns into a denial on every single shell call.

    ``local`` defaults to ``False`` for the same reason ``legacy_spec``'s does, and it is not
    a stylistic default: SPEC-04's "the path the floor stat'd is the path the child opens" is
    the assumption every identity check rests on, so a caller that forgets to say must not be
    handed it. Both call sites declare it.
    """
    import sys as _sys

    if LADDER_FLIPPED:
        raise NotImplementedError(
            "LADDER_FLIPPED is set, but rung selection is the ladder's job and the ladder is "
            "not implemented yet. This function is only the pre-flip default; the stage that "
            "flips the constant replaces it with the real selection rather than editing it."
        )
    from ..permissions_hardline._trust import select_rung

    if windows is None:
        windows = _sys.platform == "win32"
    target = Platform.WINDOWS if windows else Platform.POSIX
    spec = select_rung(ShellBlock(), _PlatformOnlyOracle(target, local), local_subject())
    assert isinstance(spec, ShellSpec)  # the two policy-off branches always return a spec
    return spec


class _PlatformOnlyOracle:
    """The two questions rung selection asks before either policy-off rung is chosen.

    Not a partial ``IdentityOracle`` by oversight: SPEC-05c says an oracle missing any method
    leaves a *policy-on* rung unattested, and this object exists only to reach the two rungs
    that ask an oracle nothing. Answering more would be claiming answers about a machine's
    access masks that nothing here has looked at.
    """

    def __init__(self, target: Platform, local: bool) -> None:
        self._target = target
        self._local = local

    def target_platform(self) -> Platform:
        return self._target

    def target_filesystem_is_local(self) -> Optional[bool]:
        return self._local


# ------------------------------------------------------------ launch requests


@dataclass(frozen=True, kw_only=True)
class _Attested:
    """LAUNCH-01: the four fields both attested variants share. LegacyLaunch has none of them."""

    workdir: AbsPath  # this call's working directory; the dialect-encoded form lives in the command line
    env: FrozenEnv  # the complete child environment; the executor sets it verbatim or refuses
    execution_subject: Subject
    attested_images: Tuple[ResolvedImage, ...]  # evidence the executor MUST re-check (LAUNCH-01d)
    spec_fingerprint: Sha256  # SPEC-08


@dataclass(frozen=True, kw_only=True)
class PosixLaunch(_Attested):
    executable: AbsPath
    argv: Tuple[str, ...]
    cwd: AbsPath  # the launcher's own directory (LAUNCH-09), not the call's working directory


@dataclass(frozen=True, kw_only=True)
class WindowsLaunch(_Attested):
    application_name: AbsPath  # lpApplicationName (LAUNCH-03)
    command_line: str
    cwd: AbsPath


AttestedLaunch = Union[PosixLaunch, WindowsLaunch]


@dataclass(frozen=True, kw_only=True)
class LegacyLaunch:
    """The two policy-off rungs (LADDER-05, SPEC-03): field-for-field today's request.

    It shares no field base with the attested variants deliberately. Adding a variant to a
    union is not the same as splitting the fields, and a ``LegacyLaunch`` that inherited
    ``attested_images`` would be carrying evidence nobody produced and an obligation the
    rule explicitly exempts it from.
    """

    command: str  # today's command string
    cwd: AbsPath  # this call's working directory — there is no launcher directory to use
    env: FrozenEnv  # today's environment: build_child_env(), inherited minus credentials
    spec_fingerprint: Sha256


LaunchRequest = Union[PosixLaunch, WindowsLaunch, LegacyLaunch]


# ------------------------------------------------------------ the decided call


@dataclass(frozen=True)
class DecidedCall:
    """SPEC-08a: what this call was decided on, frozen together in one record.

    The point is that ``launch()`` has no second source for any of it. Binding the spec but
    not the body would leave a channel that decides ``Get-Date`` and launches other text
    through the same plan, with the length guard, the attestation and the environment checks
    all bound to the stale input.
    """

    spec: ShellSpec
    body: str  # the text the floor scanned, byte for byte
    cwd: AbsPath  # the canonical working directory the decision was made against
    verdict: Verdict  # SPEC-08b: a record whose verdict is DENY refuses at launch too
    child_env: Optional[FrozenEnv] = None  # ENV-06; None for the two policy-off rungs
    attested_images: Tuple[ResolvedImage, ...] = ()


class LaunchRefused(Exception):
    """LAUNCH-01b: a launch-stage refusal, carrying the floor's own reason vocabulary.

    Raised rather than returned because it must not reach the model as an ordinary tool
    error. A tool error is something the model retries; this is a denial, and it surfaces
    through the same channel as a floor DENY so that the rule about a floor DENY not being
    maskable applies to it too.
    """

    def __init__(self, deny: Deny) -> None:
        super().__init__(deny.reason)
        self.deny = deny


@runtime_checkable
class ShellSpecProvider(Protocol):
    """TOOL-01: anything registered under the shell tool's name must expose this.

    The floor gates on the tool *name*, which is its only hook, so a replacement tool that
    does not answer this question leaves the floor without a dialect — scanning cmd syntax
    with POSIX patterns and reporting a clean result.
    """

    @property
    def shell_spec(self) -> Union[ShellSpec, Exhausted]:
        ...
