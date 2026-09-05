r"""The closed runnable set: what each command in a body is, and whether it may run.

PR-4 of the PowerShell ladder. This is where the pieces the other modules deliver separately
meet: ``EFF-01``..``EFF-08``'s effect flags, ``NAME-01``..``NAME-03``'s name resolution,
``IMG-02``'s two-halves test, ``WRAP-01``..``WRAP-06``'s re-entry and ``LAUNCH-08``'s
measurements. Each rule is defined once in ``docs/design/powershell-support-spec.zh.md`` §2.

**Two halves close the set, and neither substitutes for the other** (IMG-02). The *name* half
asks whether the command word has an entry in this dialect's trusted table, carrying the
effect flags somebody had to write down and defend. The *image* half asks whether the file the
child will open lives somewhere the subject cannot replace. A name with no image is a
``git.exe`` copied into the work tree; an image with no name is a program in a trusted
directory that nobody has classified.

**A command's effects are read off its own registration, never guessed.** An entry with empty
trigger tuples is an assertion: given any arguments, this command binds no name, writes no
environment variable, and runs nothing supplied on its own command line. Everything that
follows — the exit state, the recursion into an evaluator's literal argument, the refusal of a
word after a rebinding one — is arithmetic on those assertions.

**Nothing here is reachable in production yet.** Every rung constructible today is policy-off,
and a policy-off rung returns before this module is consulted (``LADDER-05``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Dict, Optional, Sequence, Tuple, Union

from ..capabilities.shell_spec import (
    AbsPath,
    DecidedCall,
    Deny,
    Exhausted,
    FrozenEnv,
    PASS,
    Pass,
    ResolvedImage,
    ShellDialect,
    ShellSpec,
    Sha256,
    opaque,
    validate,
)
from ._effects import (
    IN_PROCESS_KINDS,
    Dynamic,
    EffectFlag,
    ExitState,
    Literal,
    Token,
    TrustedEntry,
    names_provider_drive,
)
from ._trust import (
    NameResolution,
    child_env,
    encode_workdir,
    filtered_path_entries,
    has_lone_surrogate,
    oracle_complete,
    oversize_reason,
    read_env_inputs,
    request_for,
    resolve_name,
    trusted_image,
)
from ._wrappers import is_spawner, nested_launch

Verdict = Union[Pass, Deny]

MAX_ANALYSIS_DEPTH = 16
"""EFF-03: a ceiling on re-entry. The body is untrusted input, and a stack overflow is not a
refusal — the same reason ``MAX_REPARSE_DEPTH`` exists one module over."""


class CommandKind(Enum):
    simple = "simple"
    interpreter_launch = "interpreter_launch"  # WRAP-01
    spawner = "spawner"  # WRAP-05


@dataclass(frozen=True, kw_only=True)
class Command:
    """One simple command, as the body's own grammar cut it."""

    word: Token
    args: Tuple[Token, ...] = ()
    kind: CommandKind = CommandKind.simple
    callee_dialect: Optional[ShellDialect] = None  # interpreter_launch: WRAP-02 / WRAP-03
    inner_body: Optional[str] = None  # interpreter_launch: already base64-decoded
    literal_target: Optional[str] = None  # WRAP-04 4a: a literal string an evaluator runs


@dataclass(frozen=True)
class Opaque:
    """Refused before any command was formed — the split itself did not succeed."""

    reason: str


@dataclass(frozen=True)
class Analysis:
    """EFF-03's return type: the verdict, what the body leaves behind, and what it proved."""

    verdict: Verdict
    exit_state: ExitState
    attested: Tuple[ResolvedImage, ...]


# ------------------------------------------------------------------ analyse


def _command_from(words: Sequence[str], dialect: ShellDialect) -> Command:
    """One argv, classified. Every token is a :class:`Literal` by the time it gets here.

    That is a property of the gates, not an assumption: each dialect's split refuses every
    dynamic form before producing words (cmd refuses any expansion anywhere, PowerShell's
    lowering refuses a non-literal bare word, and the bash gate refuses the expansions that
    change argv). A dialect that later learns to carry a ``Dynamic`` through will fail the
    command-word check in :func:`analyse_body`, which is the safe direction.
    """
    word, args = words[0], tuple(words[1:])
    if is_spawner(word, args, dialect):
        return Command(word=Literal(word), args=tuple(Literal(a) for a in args),
                       kind=CommandKind.spawner)
    nested = nested_launch(word, args)
    if nested is not None:
        return Command(
            word=Literal(word), args=tuple(Literal(a) for a in args),
            kind=CommandKind.interpreter_launch,
            callee_dialect=nested.callee, inner_body=nested.body,
        )
    return Command(word=Literal(word), args=tuple(Literal(a) for a in args))


def analyse(dialect: ShellDialect, body: str) -> Union[Tuple[Command, ...], Opaque]:
    """The body's commands, or the reason it has no readable ones.

    Each dialect's own gate runs first and owns the reason: CMD-01 and the cmd token rule,
    LOWER-01's ten steps, BASH-01's syntax gate. Only a body all of those accept is split, and
    that ordering is what makes the splitter afterwards small enough to be obviously right.
    """
    if dialect is ShellDialect.CMD:
        from ._cmd import commands_of, scan_cmd

        refusal = scan_cmd(body)
        if refusal is not None:
            return Opaque(refusal)
        return tuple(_command_from(w, dialect) for w in commands_of(body))
    if dialect is ShellDialect.POWERSHELL:
        from ._powershell import LoweringError, commands_of, scan_powershell

        refusal = scan_powershell(body)
        if refusal is not None:
            return Opaque(refusal)
        try:
            lowered = commands_of(body)
        except LoweringError as exc:  # pragma: no cover - scan_powershell already refused
            return Opaque(f"hardline:powershell-opaque:{exc.step}:{exc.detail}")
        return tuple(_command_from(w, dialect) for w in lowered)
    if dialect is ShellDialect.POSIX:
        from ._bash import commands_of, scan_bash

        refusal = scan_bash(body)
        if refusal is not None:
            return Opaque(refusal)
        words = commands_of(body)
        if words is None:  # pragma: no cover - scan_bash refuses unterminated quoting first
            return Opaque("hardline:posix-opaque:BASH-01:unterminated-quote")
        return tuple(_command_from(w, dialect) for w in words)
    return Opaque("hardline:unknown-dialect-opaque")


def _literal_target(command: Command, entry: TrustedEntry) -> Optional[str]:
    """WRAP-04 4a: the literal string an evaluator will run, when there is exactly one.

    A file path, a pipe, or anything not statically known answers ``None`` and the caller
    refuses — re-entering on text this floor cannot see is not analysis, it is a guess.
    """
    if not entry.reenters:
        return None
    literals = [a.text for a in command.args if isinstance(a, Literal)]
    if len(literals) != len(command.args) or len(literals) != 1:
        return None
    return literals[0]


# ------------------------------------------------------------------ EFF-03


def analyse_body(
    spec: ShellSpec,
    body: str,
    search_path: Tuple[AbsPath, ...],
    depth: int = 0,
) -> Analysis:
    """EFF-03's recursive unit: a verdict, an exit state, and the images this body proved.

    Commands are judged **in body order**, and a rebinding command refuses everything after
    it rather than tainting a set nobody reads: once a name in this shell can mean something
    else, no later command word can be resolved by a table.
    """
    if depth > MAX_ANALYSIS_DEPTH:
        return Analysis(
            opaque(spec.dialect, "EFF-03", "reenter-depth"), ExitState(tainted=False), ()
        )
    commands = analyse(spec.dialect, body)
    if isinstance(commands, Opaque):
        return Analysis(Deny(commands.reason), ExitState(tainted=False), ())
    oracle = spec.identity_oracle
    state = ExitState(tainted=False)
    attested: Tuple[ResolvedImage, ...] = ()
    for command in commands:
        if state.tainted:
            return Analysis(opaque(spec.dialect, "EFF-02", "rebinds_after"), state, attested)
        if isinstance(command.word, Dynamic):
            return Analysis(opaque(spec.dialect, "TOK-02"), state, attested)
        if command.kind is CommandKind.interpreter_launch:
            # WRAP-01 rule 2: re-entry buys a better reason, never an approval. The nested
            # body is analysed only so a dangerous one is refused by its *own* reason.
            inner = analyse_body(
                _reenter(spec, command.callee_dialect or ShellDialect.UNKNOWN),
                command.inner_body or "", search_path, depth + 1,
            )
            verdict = (
                inner.verdict if isinstance(inner.verdict, Deny)
                else opaque(spec.dialect, "WRAP-01", "nested-launch")
            )
            return Analysis(verdict, state, attested)
        if command.kind is CommandKind.spawner:
            return Analysis(
                opaque(spec.dialect, "WRAP-05", _spawner_reason(command)), state, attested
            )
        if oracle is None or not oracle_complete(oracle):
            # SPEC-05c: without the oracle nothing can answer the image half, and the name
            # half alone is what lets a copied `git.exe` run.
            return Analysis(opaque(spec.dialect, "IMG-02", "image"), state, attested)
        resolution = resolve_name(command.word.text, spec, oracle, search_path)
        if resolution.opaque is not None:
            return Analysis(
                opaque(spec.dialect, "IMG-02", resolution.opaque), state, attested
            )
        entry = resolution.entry
        if entry is None:  # pragma: no cover - resolve_name answers one or the other
            return Analysis(opaque(spec.dialect, "EFF-04"), state, attested)
        if any(
            isinstance(a, Dynamic)
            for index, a in enumerate(command.args)
            if index in entry.predicate_positions
        ):
            # EFF-06 before the flags and before the dangerous table: both read literal
            # argument shapes, and ``ArgPattern.matches`` states that it assumes the caller
            # has already judged this — skip it and `Remove-Item $flags C:\` matches no
            # trigger at all and reads as inert.
            return Analysis(opaque(spec.dialect, "EFF-06"), state, attested)
        image = _image_for(spec, resolution, entry, oracle, search_path)
        if image is None:
            return Analysis(opaque(spec.dialect, "IMG-02", "image"), state, attested)
        attested = (*attested, image)
        if spec.dialect is ShellDialect.POWERSHELL and names_provider_drive(command.args):
            # EFF-05 after the lookup and before the flags: it is a refusal, not a flag.
            return Analysis(opaque(spec.dialect, "EFF-05"), state, attested)
        effects = entry.flags(command.args)
        if EffectFlag.executes_input in effects:
            target = _literal_target(command, entry)
            if target is None:
                return Analysis(
                    opaque(spec.dialect, "EFF-02", "executes_input"), state, attested
                )
            inner = analyse_body(spec, target, search_path, depth + 1)
            attested = (*attested, *inner.attested)
            if isinstance(inner.verdict, Deny):
                return Analysis(inner.verdict, state, attested)
            if EffectFlag.rebinds_caller in effects:
                state = state.merge(inner.exit_state)
        if EffectFlag.rebinds_after in effects:
            state = ExitState(tainted=True)
    return Analysis(PASS, state, attested)


def _reenter(spec: ShellSpec, callee: ShellDialect) -> ShellSpec:
    """WRAP-01 rule 1: the same rung read with the callee's grammar, **for the reason only**.

    The pair (dialect, rung) it produces is not a legal one, and it is not meant to be: this
    object exists to give a dangerous nested body its own refusal, and rule 2 makes the launch
    opaque whatever comes back. The fingerprint is blanked rather than recomputed so that
    nothing can mistake it for a spec that was ever attested — a recomputed one would look
    exactly like a launchable spec for an illegal rung.
    """
    return replace(spec, dialect=callee, fingerprint=Sha256(""))


def _spawner_reason(command: Command) -> str:
    """WRAP-05 / WRAP-06: the refusal names which spawner, so a reader can act on it."""
    word = command.word.text if isinstance(command.word, Literal) else "?"
    return f"WRAP-05:{word.lower()}"


def _image_for(
    spec: ShellSpec,
    resolution: NameResolution,
    entry: TrustedEntry,
    oracle: object,
    search_path: Tuple[AbsPath, ...],
) -> Optional[ResolvedImage]:
    """IMG-02's image half for one command word.

    An in-process entry binds the rung's **attested** launcher image (IMG-07), which was
    checked when the spec was built. The ACL chain is not re-walked per command word: that
    would be an oracle round trip per ancestor per word, and the residual — an install root
    that becomes writable *after* construction, contents unchanged — is recorded in the
    specification's own residual line rather than paid for here.
    """
    if entry.kind in IN_PROCESS_KINDS:
        launcher = spec.launcher
        return launcher.image if launcher is not None else None
    # An alias never arrives here. ``resolve_name`` resolves it at name-resolution time —
    # onto the target's entry when the target is in-process, and onto the *target's* image
    # when it is an external program (NAME-02, G04-36). Resolving it later, at image time,
    # would let a trusted alias name launder an untrusted target for one whole step.
    image = resolution.image
    if image is None:
        return None
    if not trusted_image(
        image, spec.execution_subject, spec.allowlist, oracle, spec.target_platform  # type: ignore[arg-type]
    ):
        return None
    return image


# ------------------------------------------------------------------ LAUNCH-08 / the floor


def merge_images(
    images: Sequence[ResolvedImage],
) -> Optional[Tuple[ResolvedImage, ...]]:
    """One entry per canonical path, and a disagreement is a refusal rather than a winner.

    ``setdefault`` would keep whichever was seen first. When the launcher record frozen at
    construction and an image resolved during analysis name the same path with different
    identities, keeping the older one hands the executor a stale identity to re-check — which
    is precisely the situation LAUNCH-01d exists to catch, not a duplicate to drop.
    """
    seen: Dict[AbsPath, ResolvedImage] = {}
    for image in images:
        prior = seen.get(image.canonical_path)
        if prior is None:
            seen[image.canonical_path] = image
            continue
        if (
            prior.filesystem_identity != image.filesystem_identity
            or prior.content_identity != image.content_identity
        ):
            return None
    return tuple(seen.values())


def floor(
    spec: ShellSpec,
    body: str,
    cwd: AbsPath,
    env: Optional[FrozenEnv],
    search_path: Tuple[AbsPath, ...],
    *,
    closed_set: bool = True,
) -> Analysis:
    """The policy-on floor: the launch measurements first, then the closed-set analysis.

    Length is measured before anything is analysed because the measurement is of the command
    line agentao will actually build, and a body that cannot be launched has no reading worth
    computing. Truncation is never an option: cut inside cmd's ``/s`` quoting and the
    structure the floor analysed stops being the structure cmd runs.
    """
    no_state = ExitState(tainted=False)
    launcher = spec.launcher
    if launcher is None or env is None:
        return Analysis(Deny("hardline:unknown-rung-opaque"), no_state, ())
    if has_lone_surrogate(body) or any(
        has_lone_surrogate(k) or has_lone_surrogate(v) for k, v in env.items()
    ):
        # LAUNCH-08e, before any measurement: all three encode first, and an exception is not
        # a verdict on the DENY channel.
        return Analysis(opaque(spec.dialect, "LAUNCH-08e", "lone-surrogate"), no_state, ())
    workdir_literal = encode_workdir(cwd, spec.dialect)  # LAUNCH-09
    if workdir_literal is None:
        return Analysis(opaque(spec.dialect, "LAUNCH-09", "launch-cwd"), no_state, ())
    request = request_for(spec, launcher, body, workdir_literal, env, cwd, ())
    if request is None:
        return Analysis(Deny("hardline:unknown-rung-opaque"), no_state, ())
    reason = oversize_reason(spec, request)
    if reason is not None:
        return Analysis(opaque(spec.dialect, "LAUNCH-08", reason), no_state, ())
    if not closed_set:
        # The floor is off. The guards above still ran — they answer whether a command line
        # can be built, not whether it should — and the launcher still has to be attested, so
        # the caller adds it. What is skipped is the judgement.
        return Analysis(PASS, no_state, ())
    return analyse_body(spec, body, search_path)


# ------------------------------------------------------------------ §4, one record


def decided_call(
    spec: ShellSpec,
    body: str,
    cwd: AbsPath,
    todays_floor: Optional[str],
    *,
    closed_set: bool = True,
) -> DecidedCall:
    """SPEC-08a: this call's inputs and its conclusion, frozen together, written once.

    ``todays_floor`` is the reason the pre-existing regex floor gave, or ``None``. It runs for
    every rung and is not replaced by the closed set: the dangerous classes are about what a
    command destroys, and the closed set is about whether it may run at all. A policy-on rung
    passes both or neither.

    The launcher's image joins the attested set unconditionally (LAUNCH-01). It is the direct
    target being started, whatever the body happens to contain — an empty body and a
    comment-only body launch the same interpreter.

    ``closed_set=False`` is a host that disabled the floor (``enable_hardline=False``). It
    suppresses the *judgement* and nothing else: the environment and the attested set are
    still computed, because they are launch inputs rather than verdicts, and the LAUNCH-08 and
    LAUNCH-09 guards still run, because "this command line cannot be built" is not a policy
    question — dropping those would trade a denial for a ``UnicodeEncodeError`` or a
    ``CreateProcessW`` failure, which is the outcome LAUNCH-08e exists to prevent.
    """
    if todays_floor is not None:
        return DecidedCall(spec=spec, body=body, cwd=cwd, verdict=Deny(todays_floor))
    if not isinstance(spec, ShellSpec):
        # An ``Exhausted`` provider or a missing spec reaches here only if the caller skipped
        # the floor above, which already refuses both. Answering with a verdict rather than an
        # ``AttributeError`` keeps the promise that this path always returns a decision
        # (method rule 22: an exception inside the floor is not a verdict).
        return DecidedCall(spec=spec, body=body, cwd=cwd, verdict=Deny("hardline:unknown-rung-opaque"))
    if not spec.policy_enabled:
        return DecidedCall(spec=spec, body=body, cwd=cwd, verdict=PASS)
    bad = validate(spec)  # before any oracle or environment work (SPEC-01 / 02 / 03)
    if bad is not None:
        return DecidedCall(spec=spec, body=body, cwd=cwd, verdict=Deny(bad))
    oracle = spec.identity_oracle
    if spec.pinned_env is None or oracle is None or not oracle_complete(oracle):
        return DecidedCall(
            spec=spec, body=body, cwd=cwd, verdict=opaque(spec.dialect, "SPEC-05c")
        )
    inputs = read_env_inputs(spec, cwd)
    if isinstance(inputs, Exhausted):
        return DecidedCall(
            spec=spec, body=body, cwd=cwd, verdict=opaque(spec.dialect, "ENV-06", inputs.reason)
        )
    search_path = filtered_path_entries(  # ENV-01a: filtered once, used by both readers
        spec.execution_subject, inputs.path_entries, inputs.cwd, inputs.project_root,
        spec.target_platform, oracle,  # type: ignore[arg-type]
    )
    env = child_env(spec, spec.pinned_env, inputs, search_path)
    result = floor(spec, body, cwd, env, search_path, closed_set=closed_set)
    launcher_image = (spec.launcher.image,) if spec.launcher is not None else ()
    images = merge_images(launcher_image + result.attested)
    verdict = result.verdict if images is not None else opaque(spec.dialect, "IMG-02", "image")
    return DecidedCall(
        spec=spec, body=body, cwd=cwd, verdict=verdict,
        child_env=env, attested_images=images or (),
    )
