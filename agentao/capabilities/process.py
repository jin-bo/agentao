"""Hardened subprocess execution shared across the runtime.

A plain ``subprocess.run(..., timeout=)`` is unsafe for an embedded /
ACP-over-stdio host in two ways this module closes:

1. **Timeout reaps only the direct child.** On timeout ``subprocess.run``
   calls ``Popen.kill()``, which signals just the immediate child. A
   process that forked grandchildren (``git`` spawning credential helpers
   on Windows, a user hook running ``mytool &``) leaves them alive — and
   because they inherit the captured pipe's write end, ``communicate()``
   never sees EOF and the caller hangs far past the timeout.
2. **Inherited stdin.** With no ``stdin=`` the child inherits the host's
   stdin, which over ACP is the JSON-RPC channel — a child that reads it
   (a credential prompt) steals protocol bytes.

:func:`run_captured` runs the child in its own process group / session,
detaches or feeds stdin explicitly, and on timeout kills the *whole* tree
before re-raising :class:`subprocess.TimeoutExpired` so callers fall back
exactly as they did under ``subprocess.run``.

``LocalShellExecutor`` (``capabilities/shell.py``) keeps its own
reader-thread run loop for streaming + inactivity-timeout semantics, but
shares :func:`kill_process_tree` for teardown; batch callers
(``search_file_content``, plugin hook commands) use :func:`run_captured`.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any, Dict, Optional, Sequence, Union

__all__ = ["run_captured", "kill_process_tree", "build_child_env", "HARNESS_ENV_KEYS"]


# Environment variables that carry *agentao's own* provider credentials.
# These are the harness's keys, not the user's: the agent is a separate
# principal from the person running it, and nothing an LLM decides to run
# needs the key that pays for the LLM. Stripping them means a prompt-
# injected ``run_shell_command("env")`` yields nothing worth stealing.
#
# Deliberately narrow. Scrubbing the user's *other* secrets (AWS, GitHub,
# database URLs) is the host's call, not the harness's — a host that wants
# a tighter environment can already pass an explicit ``env`` through
# ``ShellRequest``, and guessing here would break far more than it fixed.
# ``GOOGLE_API_KEY`` is deliberately absent: it is the standard name for
# Maps / Drive / YouTube credentials too, so stripping it would break user
# scripts the agent was asked to run, with a 401 that points nowhere near
# agentao. Gemini's own ``GEMINI_API_KEY`` is unambiguous and is stripped.
HARNESS_ENV_KEYS: frozenset = frozenset({
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
    "LLM_API_KEY",
    "LLM_EXTRA_BODY",
})


def _provider_key_names(source: Any) -> set:
    """Provider-prefixed key names implied by ``LLM_PROVIDER``.

    ``embedding/factory.py`` resolves the API key as
    ``f"{provider}_API_KEY"``, so the credential a user on an unlisted
    provider actually supplies (``LLM_PROVIDER=QWEN`` → ``QWEN_API_KEY``)
    never appears in :data:`HARNESS_ENV_KEYS`. Hard-coding a list would
    silently give exactly those users no scrubbing at all, which is worse
    than not scrubbing — they would read the docs and believe they were
    covered. Derive the name the same way the factory does.
    """
    provider = str(source.get("LLM_PROVIDER", "")).strip().upper()
    if not provider or not provider.replace("_", "").isalnum():
        return set()
    return {f"{provider}_API_KEY"}


# Escape hatch. Running ``agentao run`` — or any script that calls the
# provider — from inside the agent's own shell is a legitimate workflow
# that this scrubbing breaks. Set to "0"/"false"/"no" to restore full
# inheritance.
_SCRUB_OPT_OUT_VAR = "AGENTAO_SCRUB_CHILD_ENV"


def build_child_env(
    overrides: Optional[Dict[str, str]] = None,
    *,
    base: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Return the environment to hand a child process.

    Copies ``base`` (default ``os.environ``), drops
    :data:`HARNESS_ENV_KEYS`, then applies ``overrides`` — which are
    applied *after* the drop, so a caller that deliberately wants a
    provider key in the child can still pass one explicitly.

    Opt out with ``AGENTAO_SCRUB_CHILD_ENV=0`` to restore the previous
    full-inheritance behavior.

    This is defense in depth, not a seal: an agent that can run arbitrary
    shell commands can still ``cat .env``. It closes the cheapest path
    (``env``), not every path.
    """
    source = os.environ if base is None else base
    opt_out = str(source.get(_SCRUB_OPT_OUT_VAR, "")).strip().lower()
    if opt_out in {"0", "false", "no", "off"}:
        env = dict(source)
    else:
        drop = HARNESS_ENV_KEYS | _provider_key_names(source)
        env = {k: v for k, v in source.items() if k not in drop}
    if overrides:
        env.update(overrides)
    return env


def kill_process_tree(proc: "subprocess.Popen[Any]") -> None:
    """Best-effort kill of ``proc`` *and every descendant it spawned*.

    ``Popen.kill()`` only signals the direct child, so a timed-out process
    that forked helpers leaves grandchildren holding the inherited pipe's
    write end, which keeps ``communicate()`` blocked. We address the whole
    tree: ``taskkill /T`` on Windows, the process group via ``killpg``
    elsewhere.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=5,
            )
            return
        except Exception:
            pass  # fall through to the single-process kill below
    else:
        # ``start_new_session=True`` made the child a session/group leader,
        # so its pgid == its pid. Use the pid directly rather than
        # ``os.getpgid(pid)``: if the direct child already exited (leaving a
        # grandchild holding the pipe), getpgid on the zombie can fail and
        # we'd lose the whole group. The group id stays valid while any
        # member lives, so ``killpg(pid)`` still reaps the grandchild.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except Exception:
            pass  # fall through to the single-process kill below
    try:
        proc.kill()
    except Exception:
        pass


def run_captured(
    cmd: Union[Sequence[str], str],
    *,
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
    input: Optional[str] = None,
    shell: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> "subprocess.CompletedProcess[str]":
    """Run ``cmd`` capturing text stdout/stderr, hardened for timeouts.

    Behaves like ``subprocess.run(capture_output=True, text=True, ...)``
    with three differences that matter for an embedded / ACP-over-stdio
    host:

    - The child leads its own process group / session, so a timeout can
      reap the *entire* tree (see :func:`kill_process_tree`) rather than
      just the direct child.
    - stdin is handled explicitly: ``input`` is fed over a pipe when
      given, otherwise stdin is detached (``DEVNULL``) so a child can
      never read — and thereby steal — the host's stdin stream.
    - Output is decoded with ``errors="replace"`` so a non-UTF-8 line
      can't raise ``UnicodeDecodeError`` (which is neither
      ``SubprocessError`` nor ``OSError``, and would escape callers'
      ``except`` clauses and abort the whole operation).

    On timeout the process tree is killed, the pipes drained, and the
    original :class:`subprocess.TimeoutExpired` re-raised. Spawn failures
    (missing binary) propagate as ``FileNotFoundError`` / ``OSError`` from
    :class:`subprocess.Popen`, exactly as ``subprocess.run`` would.
    """
    popen_kwargs: Dict[str, Any] = {
        "cwd": cwd,
        # An explicit ``env`` is the caller's decision; otherwise scrub the
        # harness's own provider keys. Plugin hooks and ``search_file_content``
        # both run through here, and a hook that dumps its environment on
        # error (a ``set -x`` script, a wrapper echoing os.environ) would
        # otherwise write the live provider key into output that is injected
        # straight back into the model's context.
        "env": env if env is not None else build_child_env(),
        # Feed ``input`` over a pipe when provided; otherwise detach stdin.
        "stdin": subprocess.PIPE if input is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "errors": "replace",
        "shell": shell,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        # The group is dead now, so the inherited pipe write ends are
        # released and this second drain returns promptly.
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
