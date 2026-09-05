"""ShellExecutor capability protocol and local default.

Wraps the foreground / background subprocess machinery used by
:class:`agentao.tools.shell.ShellTool` so embedded hosts can route
shell execution through Docker, a remote runner, or an audit proxy
without monkey-patching subprocess.

The default :class:`LocalShellExecutor` shells out via ``subprocess.Popen``
with the same flags (process-group leadership, stdin detach,
inactivity-timeout reads) as the pre-capability tool, so behavior is
byte-equivalent.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from .process import build_child_env, kill_process_tree
from .shell_spec import (
    AttestedLaunch,
    Deny,
    Exhausted,
    FsId,
    HashPin,
    LaunchRefused,
    LaunchRequest,
    LegacyLaunch,
    PosixLaunch,
    PublisherTrust,
    ResolvedImage,
    Sha256,
    ShellSpec,
    WindowsLaunch,
    default_spec,
)

IS_WINDOWS = sys.platform == "win32"


@lru_cache(maxsize=1)
def resolve_shell_executable() -> Optional[str]:
    """Path to pass as ``Popen(shell=True, executable=...)``, or ``None``.

    Python hardcodes ``/bin/sh`` for ``shell=True`` on POSIX, so without
    this every ``run_shell_command`` ran under a POSIX shell — dash on most
    Linux distributions — while the tool description promised the model
    bash. Resolving bash explicitly makes the promise true rather than
    walking it back: bashisms the model reaches for by default (process
    substitution ``<(...)``, ``${var//x/y}``) now work everywhere instead
    of only where ``/bin/sh`` happens to be bash.

    ``None`` means "keep Python's default", and it is the whole reason this
    returns an Optional instead of a constant: minimal images (Alpine,
    distroless) ship ``/bin/sh`` and no bash at all, where a hardcoded
    ``executable`` would turn every shell command into a
    ``FileNotFoundError``. Degrading to ``/bin/sh`` is correct there — but
    the *description* has to degrade with it, which is why
    :class:`agentao.tools.shell.ShellTool` builds its text from this
    function rather than from a literal.

    ``/bin/bash`` wins over a PATH lookup so the choice does not shift when
    a user installs a newer bash under ``/opt/homebrew`` or ``/usr/local``.
    Windows is untouched: ``shell=True`` there means ``%COMSPEC% /c``, and
    ``executable=`` would replace cmd.exe rather than select a dialect.
    """
    if IS_WINDOWS:
        return None
    if os.path.isfile("/bin/bash") and os.access("/bin/bash", os.X_OK):
        return "/bin/bash"
    return shutil.which("bash")


def shell_display_name() -> str:
    """The shell that will actually interpret a command, for display.

    Never ``None`` — falls back to the POSIX default that
    :func:`resolve_shell_executable` returning ``None`` selects.
    """
    if IS_WINDOWS:
        return "cmd"
    return resolve_shell_executable() or "/bin/sh"


@dataclass(frozen=True, kw_only=True)
class ShellRequest:
    """A shell run, carrying the launch agentao already decided on (LAUNCH-01).

    This is the one protocol change in the PowerShell ladder, and it is deliberate: the
    request used to be a command string plus a working directory, which meant the executor
    re-derived *what* would interpret that string, at spawn time, from the platform. The
    decision and the launch could therefore disagree — the floor judged one dialect and the
    process ran another — and nothing in the shape made that visible.

    Now the request carries a discriminated :data:`~agentao.capabilities.shell_spec.LaunchRequest`
    that names the launch completely. ``timeout`` and ``on_chunk`` stay outside it because
    they are transport concerns, unrelated to what gets started.

    Hosts with a custom ``ShellExecutor`` read ``request.launch`` instead of
    ``request.command`` / ``request.cwd`` / ``request.env``. For today's rungs the payload is
    a :class:`~agentao.capabilities.shell_spec.LegacyLaunch` carrying exactly the three
    fields that were there before.
    """

    launch: LaunchRequest
    timeout: float = 120.0
    on_chunk: Optional[Callable[[str], None]] = None

    @property
    def command(self) -> str:
        """The command string, for the policy-off rungs that have one.

        Kept as a read-only projection rather than a field: display paths and the background
        handle want the text, and re-deriving it at each of those call sites is how two
        spellings of "the command" start to drift.
        """
        if isinstance(self.launch, LegacyLaunch):
            return self.launch.command
        if isinstance(self.launch, WindowsLaunch):
            return self.launch.command_line
        return subprocess.list2cmdline(self.launch.argv)

    @property
    def cwd(self) -> Path:
        """The directory the child starts in — the call's own for a legacy launch."""
        return Path(self.launch.cwd)


@dataclass
class ShellResult:
    """Result of a foreground shell run."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False


@dataclass
class BackgroundHandle:
    """Handle to a detached background process."""

    pid: int
    pgid: Optional[int] = None  # None on Windows
    command: str = ""
    cwd: Path = field(default_factory=lambda: Path("."))


@runtime_checkable
class ShellExecutor(Protocol):
    """IO contract for shell execution.

    Two operations: foreground ``run`` (caller waits for completion or
    inactivity timeout) and ``run_background`` (caller gets a handle
    immediately while the process continues detached). Hosts that
    cannot support real backgrounding can raise ``NotImplementedError``
    in ``run_background`` — :class:`agentao.tools.shell.ShellTool`
    surfaces it as a normal tool error.

    **Declaring the interpreter (optional).** An executor may additionally
    implement :class:`~agentao.capabilities.shell_spec.ShellSpecProvider` — a
    ``shell_spec`` property answering ``ShellSpec | Exhausted`` — because it is
    the only party that knows: a Docker or remote executor starts a different
    interpreter, on a different filesystem, as a different subject, and none of
    that is visible from here. It is deliberately *not* a member of this
    protocol: ``ShellExecutor`` is ``@runtime_checkable``, and a non-method
    member makes ``issubclass()`` against it raise ``TypeError`` outright while
    flipping ``isinstance()`` to ``False`` for every executor written before the
    member existed. An executor that does not declare one is read as reporting
    today's platform default, so those hosts keep working unchanged.
    """

    def run(self, request: ShellRequest) -> ShellResult:
        ...

    def run_background(self, request: ShellRequest) -> BackgroundHandle:
        ...


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def local_filesystem_identity(path: str) -> Optional[FsId]:
    """The identity of the file behind a name, on this machine (SPEC-04's "the same path").

    Device plus inode, which ``os.stat`` reports on Windows too. ``None`` means the question
    could not be answered, and every caller here treats that as a refusal rather than as a
    pass: an image that cannot be identified has not been shown to be the attested one.

    ``st_ino == 0`` is one of those unanswerable cases, not an identity. Python documents
    the field as identifying a file *only when non-zero*, and Windows — the platform this
    whole ladder targets — reports zero whenever the file index is unavailable (some network
    and removable volumes). Passing it through would hand every such file the identity
    ``<dev>:0``, so a swap between two of them would compare equal and the check would report
    a clean result while proving nothing.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not st.st_ino:
        return None
    return FsId(f"{st.st_dev}:{st.st_ino}")


def local_content_hash(path: str) -> Optional[Sha256]:
    """The sha256 of the file's bytes, or ``None`` if it cannot be read."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 16), b""):
                h.update(block)
    except OSError:
        return None
    return Sha256(h.hexdigest())


def _attested_entry(launch: AttestedLaunch, path: str) -> Optional[ResolvedImage]:
    return next((img for img in launch.attested_images if img.canonical_path == path), None)


def verify_attested_launch(launch: AttestedLaunch) -> Optional[Deny]:
    """LAUNCH-01d: re-check the images this launch names, immediately before spawning.

    The obligation is a MUST, not a courtesy. ``attested_images`` is evidence produced when
    the decision was made; between then and now the file behind a path can be replaced, and
    the executor is the last place that can still notice. A target with no entry in the
    evidence, an entry that no longer matches, or a check that cannot be performed all refuse.

    Two halves. The **direct target** — ``executable`` or ``application_name`` — is fully
    specified and checked here. The second half, every image argv names by path, needs to
    know which argv elements are image names, and that follows from how each dialect's argv
    is assembled, which is the trusted-resolution PR's work. Until then the conservative
    reading applies: an absolute path that resolves to an existing file must have an entry.
    Nothing constructs an attested launch yet, so this is strict by choice rather than by
    accident — the alternative is a lenient default arriving with the code.
    """
    direct = (
        launch.application_name if isinstance(launch, WindowsLaunch) else launch.executable
    )
    candidates = [str(direct)]
    if isinstance(launch, PosixLaunch):
        candidates += [a for a in launch.argv[1:] if os.path.isabs(a) and os.path.isfile(a)]
    for path in candidates:
        entry = _attested_entry(launch, path)
        if entry is None:
            return Deny(f"hardline:launch-attest:no-entry:{path}")
        live = local_filesystem_identity(path)
        if live is None:
            # "A check that cannot be performed refuses." Comparing two unanswerable
            # identities finds them equal, which reports a clean result while proving
            # nothing — and ``st_ino == 0`` is reachable on exactly the platform this ladder
            # targets, on network and removable volumes.
            return Deny(f"hardline:launch-attest:unidentifiable:{path}")
        if live != entry.filesystem_identity:
            return Deny(f"hardline:launch-attest:filesystem-identity:{path}")
        pin = entry.content_identity
        if pin is None:
            return Deny(f"hardline:launch-attest:no-content-identity:{path}")
        if isinstance(pin, HashPin):
            # ``pin.matches`` is the binding half: a pin names the path it was taken for, and
            # comparing this file's bytes against a pin minted for a *different* path proves
            # nothing about either. The live hash is the other half.
            if not pin.matches(entry) or local_content_hash(path) != pin.sha256:
                return Deny(f"hardline:launch-attest:content-identity:{path}")
            continue
        if isinstance(pin, PublisherTrust):
            # "A check that cannot be performed refuses." Nothing here verifies a signature,
            # so accepting a publisher-trust entry would be a pass with no check behind it —
            # the one outcome this function exists to make impossible. The trusted-resolution
            # PR that can verify a signer replaces this branch with the verification.
            return Deny(f"hardline:launch-attest:unverifiable-content-identity:{path}")
        return Deny(f"hardline:launch-attest:unknown-content-identity:{path}")
    return None


def _popen_target(launch: LaunchRequest) -> Tuple[Any, Dict[str, Any]]:
    """The ``Popen`` first argument and the launch-shaped keyword arguments.

    A policy-off rung keeps ``shell=True`` because it *is* today's launch and LADDER-05
    promises it stays field-for-field identical. The attested variants never use it: naming
    the interpreter and passing the body as one argument is the whole point of having
    resolved which interpreter to run.
    """
    if isinstance(launch, LegacyLaunch):
        return launch.command, dict(
            shell=True,
            executable=resolve_shell_executable(),
            cwd=str(launch.cwd),
            env=dict(launch.env),
        )
    refusal = verify_attested_launch(launch)
    if refusal is not None:
        raise LaunchRefused(refusal)
    if isinstance(launch, WindowsLaunch):
        return launch.command_line, dict(
            shell=False,
            executable=str(launch.application_name),
            cwd=str(launch.cwd),
            env=dict(launch.env),
        )
    return list(launch.argv), dict(
        shell=False,
        executable=str(launch.executable),
        cwd=str(launch.cwd),
        env=dict(launch.env),
    )


class LocalShellExecutor:
    """Default :class:`ShellExecutor` using ``subprocess.Popen``.

    Mirrors :func:`agentao.tools.shell.ShellTool._run_foreground` /
    ``_run_background`` exactly: shell=True wrapping, stdin detach
    (so children never inherit the ACP JSON-RPC channel), process
    group leadership for clean kill, inactivity-based timeout, and
    ``taskkill`` / ``killpg`` teardown by platform.
    """

    def __init__(self) -> None:
        self._spec: "ShellSpec | Exhausted | None" = None

    @property
    def shell_spec(self) -> "ShellSpec | Exhausted":
        """The policy-off rung for this platform, with locality declared true.

        Local means one thing only: the path the child opens is the path the floor stat'd.
        This executor is the one case where that is true by construction — a container, a
        chroot or a mount namespace on the same host is not local, which is why every other
        executor has to answer for itself rather than inherit this.
        """
        if self._spec is None:
            self._spec = default_spec(local=True)
        return self._spec

    def run(self, request: ShellRequest) -> ShellResult:
        # LAUNCH-01e: both delivery faces build their child from ``request.launch`` through
        # the same helper, so the re-check cannot be skipped by picking the other one.
        target, popen_kwargs = _popen_target(request.launch)
        popen_kwargs.update(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not IS_WINDOWS:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(target, **popen_kwargs)
        except Exception as e:
            return ShellResult(
                returncode=-1,
                stdout=b"",
                stderr=f"Error starting command: {e}".encode("utf-8"),
                timed_out=False,
            )

        stdout_chunks: List[bytes] = []
        stderr_chunks: List[bytes] = []
        last_activity = [time.monotonic()]
        timed_out = [False]
        on_chunk = request.on_chunk

        def _read(stream, chunks: List[bytes]) -> None:
            for chunk in iter(lambda: stream.read(4096), b""):
                chunks.append(chunk)
                last_activity[0] = time.monotonic()
                if on_chunk and not _is_binary(chunk):
                    try:
                        on_chunk(chunk.decode("utf-8", errors="replace"))
                    except Exception:
                        pass

        t_out = threading.Thread(target=_read, args=(proc.stdout, stdout_chunks), daemon=True)
        t_err = threading.Thread(target=_read, args=(proc.stderr, stderr_chunks), daemon=True)
        t_out.start()
        t_err.start()

        timeout = request.timeout
        while proc.poll() is None:
            if time.monotonic() - last_activity[0] > timeout:
                timed_out[0] = True
                # Shared teardown: kills the whole tree (taskkill /T or
                # killpg) via the child's pid, so a grandchild holding the
                # captured pipe can't survive the kill — and sidesteps the
                # getpgid-on-a-zombie ProcessLookupError this used to hit.
                kill_process_tree(proc)
                break
            time.sleep(0.05)

        t_out.join(timeout=2)
        t_err.join(timeout=2)

        return ShellResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            timed_out=timed_out[0],
        )

    def run_background(self, request: ShellRequest) -> BackgroundHandle:
        # LAUNCH-01e again: ``is_background`` chooses which method delivers the request, and
        # nothing else. It used to choose a second spawn path with its own ``shell=True`` and
        # its own environment, which meant a rule proved about one face said nothing about
        # the other.
        target, popen_kwargs = _popen_target(request.launch)
        popen_kwargs.update(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if IS_WINDOWS:
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            )
            proc = subprocess.Popen(target, **popen_kwargs)
            return BackgroundHandle(
                pid=proc.pid,
                pgid=None,
                command=request.command,
                cwd=request.cwd,
            )

        popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(target, **popen_kwargs)
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = None
        return BackgroundHandle(
            pid=proc.pid,
            pgid=pgid,
            command=request.command,
            cwd=request.cwd,
        )
