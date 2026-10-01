"""`/acp login` — ACP Terminal Auth for a configured ACP server.

The agent advertises a ``terminal`` auth method; the CLI runs the agent's own
launch command with the method's arguments appended, interactively, and on
exit status ``0`` restarts the server and connects again (ACP v1
authentication spec: "Reconnects and reinitializes the ACP Agent").

The login process must own the user's terminal. It inherits stdin, stdout and
stderr and stays in the CLI's session. The ACP server launch flags
(``start_new_session`` on POSIX, a new process group on Windows) are
deliberately not reused: a child in a new session has no controlling
terminal, so hidden input and Ctrl+C would not reach it.

On POSIX with a controlling terminal the login runs the way a shell runs a
foreground job: in its own process group, which is given the terminal for the
duration (``tcsetpgrp``) and hands it back afterwards. Ctrl+C then reaches the
login — including what a runner such as ``npx`` / ``uvx`` started under it —
and not the CLI, and a failed or cancelled login can be cleaned up as a whole
group: killing only the runner would leave its children holding the
terminal. Elsewhere (Windows, no controlling terminal) the login shares the
CLI's process group; on Ctrl+C both see the interrupt and the CLI reaps the
login. On Windows the login also runs in a job object, which a failed or
cancelled login's leftovers are ended through — the only handle on them once
a runner has exited (:mod:`._win_job`).

The CLI prompt is not reading input while a slash command runs, so nothing
competes with the login for keystrokes.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional

from rich.markup import escape
from rich.prompt import Prompt

from .._globals import console


def _shown(value: Any) -> str:
    """*value* ready for Rich: control / bidi characters dropped, markup escaped.

    Registry metadata and agent-advertised auth methods are third-party text
    shown at an approval prompt, so both passes apply — escaping alone keeps
    the characters that reorder or hide what the user is approving.
    """
    from ...security.terminal_text import sanitize_terminal_text

    return escape(sanitize_terminal_text(str(value)))

if TYPE_CHECKING:
    from ...acp_client.auth import TerminalLoginCommand
    from ..app import AgentaoCLI

#: Seconds a login child gets to exit on its own after Ctrl+C (it received
#: the same SIGINT) before it is terminated, then killed.
_CANCEL_GRACE_S = 3.0
_TERMINATE_GRACE_S = 2.0

#: Exit statuses that mean "interrupted": killed by SIGINT, or exited 130.
_INTERRUPTED = frozenset({-int(signal.SIGINT), 128 + int(signal.SIGINT)})


@dataclass(frozen=True)
class LoginOutcome:
    """How a terminal login process ended."""

    status: str  # "ok" | "failed" | "cancelled"
    returncode: Optional[int] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def terminal_login_available() -> bool:
    """Whether this process can hand a login an interactive terminal."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _outcome(returncode: Optional[int]) -> LoginOutcome:
    if returncode == 0:
        return LoginOutcome("ok", returncode=0)
    if returncode in _INTERRUPTED:
        return LoginOutcome("cancelled", returncode=returncode)
    return LoginOutcome("failed", returncode=returncode)


# ---------------------------------------------------------------------------
# POSIX: the login as a foreground job
# ---------------------------------------------------------------------------


def _foreground_terminal() -> Optional[int]:
    """Our controlling terminal's fd, if we are its foreground process group."""
    if sys.platform == "win32":
        return None
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd) or os.tcgetpgrp(fd) != os.getpgrp():
            return None
    except (AttributeError, ValueError, OSError):
        return None
    return fd


@contextmanager
def _sigttou_blocked() -> Iterator[None]:
    # ``tcsetpgrp`` from a background process group raises SIGTTOU, which
    # stops the caller unless the signal is blocked or ignored. Blocking it
    # for this thread only works off the main thread, unlike ``signal.signal``.
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def _set_foreground(fd: int, pgid: int) -> None:
    try:
        with _sigttou_blocked():
            os.tcsetpgrp(fd, pgid)
    except OSError:
        # The group is already gone (the login exited at once), or the
        # terminal went away; there is nothing to hand over.
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _end_group(pgid: int) -> None:
    """Terminate whatever is left of the login's process group."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + _TERMINATE_GRACE_S
        while time.monotonic() < deadline:
            if not _group_alive(pgid):
                return
            time.sleep(0.05)


def _run_as_foreground_job(command: "TerminalLoginCommand", tty: int) -> LoginOutcome:
    if sys.version_info >= (3, 11):
        group: Dict[str, Any] = {"process_group": 0}
    else:  # pragma: no cover - exercised by the Python 3.10 CI job
        group = {"preexec_fn": os.setpgrp}
    try:
        proc = subprocess.Popen(command.argv, cwd=command.cwd, env=command.env, **group)
    except (OSError, ValueError) as exc:
        return LoginOutcome("failed", error=str(exc))
    pgid = proc.pid
    try:
        _set_foreground(tty, pgid)
        try:
            # It may have touched the terminal before owning it and been
            # stopped (SIGTTIN / SIGTTOU); let it continue.
            os.killpg(pgid, signal.SIGCONT)
        except OSError:
            pass
        try:
            returncode: Optional[int] = proc.wait()
        except KeyboardInterrupt:
            # Not from the terminal (the login's group owns it), but honour it.
            _end_group(pgid)
            returncode = proc.wait()
            return LoginOutcome("cancelled", returncode=returncode)
    finally:
        _set_foreground(tty, os.getpgrp())
    if returncode != 0:
        _end_group(pgid)
    return _outcome(returncode)


# ---------------------------------------------------------------------------
# Elsewhere: the login in the CLI's process group
# ---------------------------------------------------------------------------


def _reap(proc: "subprocess.Popen[Any]") -> Optional[int]:
    """Wait for a cancelled login child, escalating if it does not exit."""
    if sys.platform == "win32":
        from ...capabilities.process import kill_process_tree

        escalate = (lambda: None, lambda: kill_process_tree(proc))
    else:
        # It shares our process group, so a group kill would take the CLI
        # down with it: only the direct child is signalled here.
        escalate = (proc.terminate, proc.kill)
    steps = ((None, _CANCEL_GRACE_S), (escalate[0], _TERMINATE_GRACE_S), (escalate[1], None))
    for action, grace in steps:
        if action is not None:
            try:
                action()
            except OSError:
                pass
        try:
            return proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        except KeyboardInterrupt:
            # Another Ctrl+C while waiting: move to the next step.
            continue
    return proc.poll()


def _run_in_shared_group(command: "TerminalLoginCommand") -> LoginOutcome:
    from ._win_job import CREATE_SUSPENDED, LoginJob

    # Windows: a job object holds the login and its descendants, so a failed
    # or cancelled login is ended whole even after its runner has exited.
    job = LoginJob.create()
    extra: Dict[str, Any] = {"creationflags": CREATE_SUSPENDED} if job is not None else {}
    try:
        proc = subprocess.Popen(command.argv, cwd=command.cwd, env=command.env, **extra)
    except (OSError, ValueError) as exc:
        if job is not None:
            job.close()
        return LoginOutcome("failed", error=str(exc))
    if job is not None and not job.adopt(proc._handle):
        job.close()
        job = None
    try:
        try:
            outcome = _outcome(proc.wait())
        except KeyboardInterrupt:
            outcome = LoginOutcome("cancelled", returncode=_reap(proc))
        if not outcome.ok and job is not None:
            job.terminate()
        return outcome
    finally:
        if job is not None:
            job.close()


def run_terminal_login(command: "TerminalLoginCommand") -> LoginOutcome:
    """Run *command* attached to this terminal and report how it ended.

    Exit status ``0`` is success. A non-zero status, death by a signal, a
    launch failure, or Ctrl+C is not; Ctrl+C (SIGINT, or exit status 130)
    reports as ``cancelled``. No stdio redirection, no new session and no
    new process group / console in either mode — see the module docstring.
    """
    tty = _foreground_terminal()
    if tty is not None:
        return _run_as_foreground_job(command, tty)
    return _run_in_shared_group(command)


def auth_required_hint(mgr: Any, name: str, exc: BaseException) -> str:
    """What to tell the user after *name* answered ``auth_required``."""
    from ...acp_client.auth import auth_method_type, terminal_auth_methods

    details = getattr(exc, "details", None) or {}
    methods: List[Dict[str, Any]] = details.get("auth_methods")
    if methods is None:
        try:
            methods = mgr.auth_methods(name)
        except Exception:
            methods = []
    if terminal_auth_methods(methods) and getattr(mgr, "terminal_auth", False):
        return f"'{name}' requires authentication. Run /acp login {name}, then send again."
    kinds = sorted({auth_method_type(m) for m in methods})
    offered = f" (it offers: {', '.join(kinds)})" if kinds else ""
    return (
        f"'{name}' requires authentication{offered}. Agentao can run only "
        f"terminal logins; authenticate outside Agentao, then /acp restart {name}."
    )


def _choose_method(name: str, methods: List[Dict[str, Any]], wanted: str) -> Optional[Dict[str, Any]]:
    from ...acp_client.auth import auth_method_type, terminal_auth_methods

    terminal = terminal_auth_methods(methods)
    if wanted:
        for method in methods:
            if method["id"] == wanted:
                if auth_method_type(method) != "terminal":
                    console.print(
                        f"\n[error]Method {_shown(wanted)} is a "
                        f"{_shown(auth_method_type(method))} method; only terminal "
                        f"methods can be run here.[/error]\n"
                    )
                    return None
                return method
        console.print(f"\n[error]'{_shown(name)}' does not offer method {_shown(wanted)}.[/error]\n")
        return None
    if not terminal:
        kinds = ", ".join(sorted({auth_method_type(m) for m in methods})) or "none"
        console.print(
            f"\n[warning]'{_shown(name)}' offers no terminal login (methods: {_shown(kinds)}).[/warning]"
            f"\n[info]Authenticate outside Agentao, then /acp restart {_shown(name)}.[/info]\n"
        )
        return None
    if len(terminal) == 1:
        return terminal[0]
    console.print(f"\n[info]'{_shown(name)}' offers several terminal logins:[/info]")
    for number, method in enumerate(terminal, 1):
        label = method.get("name") or method["id"]
        console.print(f"  {number}. [cyan]{_shown(method['id'])}[/cyan]  {_shown(str(label))}")
    # Numbered choices: the ids are agent-authored, and Prompt shows its
    # choices and default verbatim — only the sanitized list above shows them.
    numbers = [str(n) for n in range(1, len(terminal) + 1)]
    choice = Prompt.ask("Method", choices=numbers, default="1")
    return terminal[int(choice) - 1]


def acp_login(cli: "AgentaoCLI", rest: str) -> None:
    """``/acp login <name> [method-id]``."""
    from ...acp_client.auth import (
        AuthMethodError,
        build_terminal_login_command,
        is_auth_required,
        terminal_auth_methods,
    )
    from ...acp_client.client import AcpClientError, AcpServerNotFound
    from .acp import _ensure_acp_manager

    parts = rest.split() if rest else []
    if not parts or len(parts) > 2:
        console.print("\n[error]Usage: /acp login <name> [method-id][/error]\n")
        return
    name, wanted = parts[0], (parts[1] if len(parts) == 2 else "")
    mgr = _ensure_acp_manager(cli)
    if mgr is None:
        return
    handle = mgr.get_handle(name)
    if handle is None:
        console.print(f"\n[error]Unknown ACP server: {_shown(name)}[/error]\n")
        return
    if not mgr.terminal_auth:
        console.print(
            "\n[error]Terminal login needs an interactive terminal; this session "
            "has none.[/error]\n"
        )
        return

    methods = mgr.auth_methods(name)
    if not methods:
        # Not handshaken yet: connect once to learn what it advertises.
        try:
            mgr.connect_server(name)
        except Exception as exc:
            if not is_auth_required(exc):
                console.print(f"\n[error]Could not reach '{_shown(name)}': {_shown(str(exc))}[/error]\n")
                return
        else:
            # ``session/new`` succeeding does not mean no login is needed:
            # an agent may ask for credentials only at ``session/prompt``.
            # Stop here only when there is no terminal login to run.
            methods = mgr.auth_methods(name)
            if not wanted and not terminal_auth_methods(methods):
                console.print(f"\n[success]'{_shown(name)}' connected; no login needed.[/success]\n")
                return
        methods = mgr.auth_methods(name)

    method = _choose_method(name, methods, wanted)
    if method is None:
        return
    try:
        command = build_terminal_login_command(handle.config, method)
    except AuthMethodError as exc:
        console.print(f"\n[error]{_shown(str(exc))}[/error]\n")
        return

    try:
        with mgr.reserve_for_login(name):
            # The argv only — the environment can carry credentials.
            console.print(
                f"\n[info]Logging in to '{_shown(name)}':[/info] "
                f"[dim]{_shown(subprocess.list2cmdline(command.argv))}[/dim]\n"
            )
            outcome = run_terminal_login(command)
            if not outcome.ok:
                if outcome.status == "cancelled":
                    console.print(f"\n[warning]Login to '{_shown(name)}' cancelled.[/warning]\n")
                elif outcome.error:
                    console.print(
                        f"\n[error]Could not start the login for '{_shown(name)}': "
                        f"{_shown(outcome.error)}[/error]\n"
                    )
                else:
                    console.print(
                        f"\n[error]Login to '{_shown(name)}' failed "
                        f"(exit status {outcome.returncode}).[/error]\n"
                    )
                return
            console.print(f"\n[dim]Login finished; restarting '{_shown(name)}'...[/dim]")
            mgr.restart_server(name)
            try:
                mgr.connect_server(name)
            except Exception as exc:
                if is_auth_required(exc):
                    console.print(
                        f"\n[error]'{_shown(name)}' still requires authentication "
                        f"after the login.[/error]\n"
                    )
                else:
                    console.print(
                        f"\n[error]'{_shown(name)}' did not reconnect: "
                        f"{_shown(str(exc))}[/error]\n"
                    )
                return
    except AcpServerNotFound:
        console.print(f"\n[error]Unknown ACP server: {_shown(name)}[/error]\n")
        return
    except AcpClientError as exc:
        console.print(f"\n[error]{_shown(str(exc))}[/error]\n")
        return
    except RuntimeError as exc:
        console.print(f"\n[error]Could not restart '{_shown(name)}': {_shown(str(exc))}[/error]\n")
        return
    console.print(f"\n[success]Logged in to '{_shown(name)}' and reconnected.[/success]\n")
