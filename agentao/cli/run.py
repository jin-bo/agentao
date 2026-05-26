"""Non-interactive ``agentao run`` subcommand.

Pipeline: spec (stdin or ``--spec``) + CLI overrides → one Agentao turn
→ structured ``text`` or ``json`` result on stdout.

``agentao -p`` is reimplemented as a thin shim over :func:`execute` so
both share one exit-code table.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .run_models import (
    PermissionModeName,
    RunErrorEnvelope,
    RunResult,
    RunSpec,
    RunUsage,
)
from .run_template import RunTemplateError, render_spec


# Same identifier rule as ``RunParameter.name`` — failing fast at the
# CLI parse stage gives a sharper error than letting it fall through to
# "unknown parameter" downstream.
_PARAM_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_INVALID_USAGE = 2
EXIT_PERMISSION_OR_INTERACTION = 3
EXIT_MAX_ITERATIONS = 4
EXIT_INTERRUPTED = 130

DEFAULT_MAX_ITERATIONS = 100

PERMISSION_MODE_CHOICES = ("read-only", "workspace-write", "full-access", "plan")


def _attach_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach the shared ``run`` flag schema to ``parser``."""
    parser.add_argument("--spec", dest="spec_path", default=None, metavar="FILE")
    parser.add_argument("--prompt", dest="prompt", default=None)
    parser.add_argument(
        "--format", dest="output_format",
        choices=["text", "json"], default=None,
    )
    parser.add_argument("--model", dest="model", default=None)
    parser.add_argument("--base-url", dest="base_url", default=None)
    parser.add_argument(
        "--permission-mode", dest="permission_mode",
        choices=list(PERMISSION_MODE_CHOICES), default=None,
    )
    parser.add_argument(
        "--interaction-policy", dest="interaction_policy",
        choices=["reject"], default=None,
    )
    parser.add_argument(
        "--max-iterations", dest="max_iterations",
        type=int, default=None,
    )
    parser.add_argument(
        "--skill", dest="skills",
        action="append", default=None, metavar="NAME",
    )
    # Tri-state: --replay (true) / --no-replay (false) / unset (use spec).
    parser.add_argument(
        "--replay", dest="replay", action="store_true", default=None,
    )
    parser.add_argument(
        "--no-replay", dest="replay",
        action="store_const", const=False,
    )
    parser.add_argument(
        "--param", dest="params", action="append", default=None,
        metavar="KEY=VALUE",
        help="Set a spec parameter. Repeatable. Example: --param depth=deep",
    )


def add_run_subparser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Register the ``run`` subparser. Called by ``entrypoints._build_parser``."""
    parser = subparsers.add_parser(
        "run",
        help="Run a single non-interactive turn from a structured spec.",
    )
    _attach_run_arguments(parser)
    return parser


# ---------------------------------------------------------------------------
# Spec loading & merge
# ---------------------------------------------------------------------------


class _UsageError(Exception):
    """Raised when CLI usage is invalid (exit 2)."""


def _stdin_has_piped_data() -> bool:
    """True only when stdin is an actual pipe / redirect / socket.

    A non-TTY stdin alone is not enough: CI environments commonly attach
    ``/dev/null`` (a character device), which would otherwise be
    misclassified as "piped" and conflict with ``--spec``.
    """
    try:
        if sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (OSError, ValueError):
        # StringIO and similar fakes have no fileno; treat the non-TTY
        # signal as piped data so unit tests still exercise the path.
        return True
    return (
        stat.S_ISFIFO(mode)
        or stat.S_ISREG(mode)
        or stat.S_ISSOCK(mode)
    )


def _load_spec(args: argparse.Namespace) -> Tuple[RunSpec, List[str]]:
    """Load a :class:`RunSpec` from --spec or stdin and return ``(spec, warnings)``."""
    warnings: List[str] = []
    spec_path = args.spec_path
    stdin_has_data = _stdin_has_piped_data()

    if spec_path is not None and stdin_has_data:
        raise _UsageError(
            "agentao run: --spec and piped stdin cannot be combined. "
            "Choose one structured spec source.",
        )

    if spec_path is not None:
        try:
            text = Path(spec_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise _UsageError(
                f"agentao run: failed to read --spec {spec_path}: {exc}"
            )
        return _parse_spec_text(text, source=str(spec_path)), warnings

    if stdin_has_data:
        text = sys.stdin.read()
        if text.strip():
            return _parse_spec_text(text, source="<stdin>"), warnings

    return RunSpec(), warnings


def _parse_spec_text(text: str, *, source: str) -> RunSpec:
    """Parse YAML-or-JSON spec text into a validated :class:`RunSpec`."""
    text = text.strip()
    if not text:
        return RunSpec()
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - exercised by clean-install smoke
        raise _UsageError(
            "agentao run: PyYAML is required to parse spec files; "
            f"install pyyaml or pass JSON only ({exc})."
        )
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _UsageError(
            f"agentao run: failed to parse spec from {source}: {exc}"
        )
    if raw is None:
        return RunSpec()
    if not isinstance(raw, dict):
        raise _UsageError(
            f"agentao run: spec {source} must be a mapping, "
            f"got {type(raw).__name__}.",
        )
    try:
        return RunSpec.model_validate(raw)
    except Exception as exc:
        raise _UsageError(
            f"agentao run: spec validation failed ({source}): {exc}"
        )


def _parse_cli_params(items: Optional[List[str]]) -> Dict[str, str]:
    """Parse repeated ``--param KEY=VALUE`` arguments into a dict.

    All errors raise :class:`_UsageError` (exit 2). Duplicate keys
    error rather than last-wins: silent override is more surprising
    than a clear "supplied multiple times" message.
    """
    if not items:
        return {}
    out: Dict[str, str] = {}
    for raw in items:
        # ``split("=", 1)`` preserves further ``=`` chars inside the
        # value — so ``--param expr=a=b`` yields ``("expr", "a=b")``.
        if "=" not in raw:
            raise _UsageError(
                f"agentao run: malformed --param {raw!r} (expected KEY=VALUE)",
            )
        key, value = raw.split("=", 1)
        if not key:
            raise _UsageError(
                f"agentao run: malformed --param {raw!r} (expected KEY=VALUE)",
            )
        if not _PARAM_KEY_RE.fullmatch(key):
            raise _UsageError(
                f"agentao run: --param {key!r} is not a valid identifier "
                "(must match [A-Za-z_][A-Za-z0-9_]*)",
            )
        if key in out:
            raise _UsageError(
                f"agentao run: --param {key!r} supplied multiple times",
            )
        out[key] = value
    return out


def _apply_cli_overrides(spec: RunSpec, args: argparse.Namespace) -> RunSpec:
    """Layer explicit CLI flags on top of the spec.

    Only flags the user explicitly provided override spec values;
    argparse defaults stay invisible so a spec field is never erased
    by an absent flag.
    """
    overrides: Dict[str, Any] = {}
    if args.prompt is not None:
        overrides["prompt"] = args.prompt
    if args.model is not None:
        overrides["model"] = args.model
    if args.base_url is not None:
        overrides["base_url"] = args.base_url
    if args.permission_mode is not None:
        overrides["permission_mode"] = args.permission_mode
    if args.interaction_policy is not None:
        overrides["interaction_policy"] = args.interaction_policy
    if args.max_iterations is not None:
        overrides["max_iterations"] = args.max_iterations
    if args.skills is not None:
        overrides["skills"] = list(args.skills)
    if args.replay is not None:
        overrides["replay"] = bool(args.replay)
    if args.output_format is not None:
        existing = spec.output.model_dump() if spec.output is not None else {}
        existing["format"] = args.output_format
        overrides["output"] = existing
    if not overrides:
        return spec
    merged = spec.model_dump(exclude_none=True)
    merged.update(overrides)
    return RunSpec.model_validate(merged)


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------


def _resolve_cwd(spec: RunSpec) -> Path:
    return Path(spec.cwd or os.getcwd()).expanduser().resolve()


def _resolve_permission_mode(name: Optional[PermissionModeName]):
    """Translate a spec mode name into the runtime enum.

    Defaults to ``workspace-write`` (matches the interactive CLI) when
    the spec leaves the field unset.
    """
    from ..permissions import PermissionMode
    return PermissionMode(name or "workspace-write")


def _serialize_result(result: RunResult, output_format: str) -> str:
    if output_format == "text":
        return result.final_text or ""
    payload = result.model_dump(exclude_none=True)
    return json.dumps(payload, ensure_ascii=False)


def _emit(result: RunResult, output_format: str) -> None:
    text = _serialize_result(result, output_format)
    if output_format == "json":
        sys.stdout.write(text + "\n")
    elif text:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    sys.stdout.flush()
    # Text-mode contract: a non-zero exit must carry a diagnostic.
    # ``final_text`` is None for any error status, so without this the
    # ``-p`` shim and ``--format text`` would exit silently on
    # permission-denied / max-iter / interrupted / runtime errors.
    # JSON mode already serializes ``error`` inline, so skip there.
    if (
        output_format == "text"
        and result.error is not None
        and result.error.message
    ):
        sys.stderr.write(f"agentao run: {result.error.message}\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------


def execute(argv: Optional[Sequence[str]] = None) -> int:
    """Top-level entry point — used by the ``-p`` shim and tests."""
    parser = argparse.ArgumentParser(prog="agentao run", add_help=True)
    _attach_run_arguments(parser)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_INVALID_USAGE
    return _execute_with_args(args)


def _emit_invalid_usage(
    message: str, output_format: str, *, cwd: Optional[str] = None,
    model: str = "",
) -> int:
    """Emit a uniform ``invalid_spec`` failure on the requested channel.

    JSON mode produces a parseable ``RunResult`` envelope so automation
    can read the error structurally; text mode keeps the bare stderr
    line that humans expect from a CLI usage error.
    """
    if output_format == "json":
        _emit(
            RunResult(
                status="error",
                session_id="",
                cwd=cwd or os.getcwd(),
                model=model,
                error=RunErrorEnvelope(
                    type="invalid_spec",
                    message=message,
                ),
            ),
            "json",
        )
    else:
        sys.stderr.write(f"{message}\n")
    return EXIT_INVALID_USAGE


def _execute_with_args(args: argparse.Namespace) -> int:
    """Pipeline body — argparse-parsed ``args`` is the only input."""
    # ``--format`` may live on either the CLI or the spec. We need a
    # best-effort answer up-front so the early invalid-usage branches
    # can still emit a JSON envelope when JSON mode was requested.
    output_format = args.output_format or "text"

    try:
        spec, warnings = _load_spec(args)
    except _UsageError as exc:
        return _emit_invalid_usage(str(exc), output_format)

    # Once the spec is loaded, prefer its declared format if the CLI
    # didn't override it.
    if (
        args.output_format is None
        and spec.output is not None
        and spec.output.format is not None
    ):
        output_format = spec.output.format

    try:
        spec = _apply_cli_overrides(spec, args)
    except Exception as exc:
        return _emit_invalid_usage(
            f"agentao run: invalid CLI override: {exc}", output_format,
        )

    if spec.output is not None and spec.output.format is not None:
        output_format = spec.output.format

    # Snapshot pre-render presence so the prompt-required diagnostic
    # below can distinguish "no prompt supplied" from "template rendered
    # to empty" — the former points at --prompt / spec.prompt, the
    # latter at the param value that produced the empty render.
    prompt_was_supplied = bool(spec.prompt)

    # Render must come after _apply_cli_overrides (so a --prompt
    # override can itself be a template against spec.parameters) and
    # before the "prompt required" check.
    try:
        cli_params = _parse_cli_params(getattr(args, "params", None))
        spec = render_spec(spec, cli_params)
    except (_UsageError, RunTemplateError) as exc:
        return _emit_invalid_usage(str(exc), output_format)

    if not spec.prompt:
        if prompt_was_supplied:
            return _emit_invalid_usage(
                "agentao run: prompt template rendered to empty; "
                "check --param values.",
                output_format,
            )
        return _emit_invalid_usage(
            "agentao run: prompt is required (set spec.prompt or pass --prompt).",
            output_format,
        )

    if spec.interaction_policy is not None and spec.interaction_policy != "reject":
        return _emit_invalid_usage(
            f"agentao run: interaction_policy={spec.interaction_policy!r} "
            "is not supported (M0 accepts only 'reject').",
            output_format,
        )

    return _run_pipeline(spec, output_format=output_format, warnings=warnings)


# ---------------------------------------------------------------------------
# Pipeline body
# ---------------------------------------------------------------------------


def _run_pipeline(
    spec: RunSpec,
    *,
    output_format: str,
    warnings: List[str],
) -> int:
    # Local imports keep ``agentao --help`` snappy and avoid pulling
    # the LLM stack when the user only inspects --help.
    from ..cancellation import CancellationToken
    from ..embedding import build_from_environment
    from ..host.models import PermissionDecisionEvent
    from ..permissions import PermissionMode
    from ..replay import ReplayConfig
    from ..transport import EventType, NonInteractiveTransport
    from .session import (
        dispatch_plugin_session_end, dispatch_plugin_session_start,
    )
    from .subcommands import _load_and_register_plugins

    cwd = _resolve_cwd(spec)
    permission_mode = _resolve_permission_mode(spec.permission_mode)
    # ``or`` would silently rewrite an explicit ``0`` to the default;
    # automation needs to keep the zero-iteration path testable.
    max_iterations = (
        DEFAULT_MAX_ITERATIONS
        if spec.max_iterations is None
        else spec.max_iterations
    )

    token = CancellationToken()
    transport = NonInteractiveTransport(token=token)

    # spec.replay is authoritative — pass an explicit ReplayConfig so
    # the factory's disk auto-load is bypassed when ``replay: false``.
    replay_enabled = bool(spec.replay)
    replay_config = ReplayConfig(enabled=replay_enabled)

    factory_kwargs: Dict[str, Any] = dict(
        working_directory=cwd,
        transport=transport,
        replay_config=replay_config,
    )
    if spec.model is not None:
        factory_kwargs["model"] = spec.model
    if spec.base_url is not None:
        factory_kwargs["base_url"] = spec.base_url
    # When the spec carries non-empty instructions, route them through
    # the existing ``project_instructions`` kwarg on Agentao. The agent
    # short-circuits the AGENTAO.md disk read for any non-None value,
    # so a guard against both ``""`` and whitespace-only output is
    # necessary to avoid silently nuking the AGENTAO.md fallback. The
    # whitespace case shows up naturally with YAML block scalars
    # (``instructions: |\n  {{ extra }}\n``) when ``extra`` is empty —
    # Jinja's ``keep_trailing_newline=True`` leaves a bare ``"\n"``.
    if spec.instructions and spec.instructions.strip():
        factory_kwargs["project_instructions"] = spec.instructions

    try:
        agent = build_from_environment(**factory_kwargs)
    except Exception as exc:
        # JSON callers expect a parseable envelope for every failure
        # mode; without this branch a missing API key would give them
        # only a stderr line + non-zero exit, breaking automation.
        _emit(
            RunResult(
                status="error",
                session_id="",
                cwd=str(cwd),
                model=spec.model or "",
                error=RunErrorEnvelope(
                    type="runtime_error",
                    message=f"failed to construct agent: {exc}",
                ),
                warnings=warnings,
            ),
            output_format,
        )
        return EXIT_RUNTIME_ERROR

    # Mirror the interactive CLI's session.py wiring: ToolExecutor reads
    # ``tool_runner._session_id`` when stamping PreToolUse/PostToolUse
    # hook payloads, so plugins lose Session correlation without this.
    try:
        agent.tool_runner._session_id = agent._session_id
    except AttributeError:  # pragma: no cover - test stubs without the attr
        pass

    # Permission mode + read-only enforcement must be synchronized at
    # both runtime sites: the engine's ``read-only`` preset is empty by
    # design — actual enforcement lives in ``ToolRunner.readonly_mode``.
    if agent.permission_engine is not None:
        agent.permission_engine.set_mode(permission_mode)
    agent.tool_runner.set_readonly_mode(
        permission_mode == PermissionMode.READ_ONLY,
    )

    if spec.permissions is not None and agent.permission_engine is not None:
        engine_allow = [r.to_engine_dict("allow") for r in spec.permissions.allow]
        engine_deny = [r.to_engine_dict("deny") for r in spec.permissions.deny]
        if engine_allow or engine_deny:
            agent.permission_engine.add_run_rules(
                allow=engine_allow,
                deny=engine_deny,
                source="run-spec",
            )

    try:
        _load_and_register_plugins(agent)
    except Exception as exc:
        warnings.append(f"plugin load: {exc}")

    if spec.skills:
        available = set(agent.skill_manager.list_available_skills())
        missing = [s for s in spec.skills if s not in available]
        if missing:
            _emit(RunResult(
                status="error",
                session_id=agent._session_id or "",
                turn_id=agent._current_turn_id,
                cwd=str(cwd),
                model=agent.llm.model,
                error=RunErrorEnvelope(
                    type="invalid_spec",
                    message="missing skill(s): " + ", ".join(repr(s) for s in missing),
                ),
                tool_calls=0,
                warnings=warnings,
            ), output_format)
            agent.close()
            return EXIT_INVALID_USAGE
        for skill_name in spec.skills:
            agent.skill_manager.activate_skill(
                skill_name, task_description=spec.prompt or "",
            )

    replay_path: Optional[str] = None
    if replay_enabled and agent.replay_manager is not None:
        try:
            path = agent.replay_manager.start(agent._session_id)
            if path is not None:
                replay_path = str(path)
        except Exception as exc:  # pragma: no cover - best-effort
            warnings.append(f"replay start: {exc}")

    dispatch_plugin_session_start(agent, agent._session_id or "")

    prev_sigint, prev_sigterm = _install_signal_handlers(token)

    def _on_event(event: Any) -> None:
        if not isinstance(event, PermissionDecisionEvent):
            return
        if event.outcome == "deny":
            if transport.rejection is None:
                transport.rejection = {
                    "type": "permission_denied",
                    "tool_name": event.tool_name,
                    "tool_call_id": event.tool_call_id,
                    "matched_rule": event.matched_rule,
                    "message": event.reason or "denied",
                }
            token.cancel(f"permission_denied: {event.tool_name}")
        elif event.outcome == "prompt":
            transport.queue_ask(event.tool_name, event.tool_call_id)

    agent.add_event_observer(_on_event)

    pre_prompt = agent.llm.total_prompt_tokens
    pre_completion = agent.llm.total_completion_tokens

    tool_calls_count = 0
    captured_turn_id: Optional[str] = None

    def _on_tool_event(event: Any) -> None:
        nonlocal tool_calls_count, captured_turn_id
        ev_type = getattr(event, "type", None)
        # ``run_turn`` clears ``agent._current_turn_id`` in its finally
        # block, so by the time we serialize RunResult the field is
        # always None. Snapshot it on TURN_BEGIN so the JSON envelope
        # can correlate the run with replay / host events.
        if ev_type == EventType.TURN_BEGIN and captured_turn_id is None:
            captured_turn_id = getattr(agent, "_current_turn_id", None)
            return
        # ToolExecutor fires TOOL_START *before* the deny check, so
        # counting that event would over-report denied / user-cancelled
        # tools. TOOL_COMPLETE always fires last, with status set to
        # "ok" / "error" / "cancelled" — count the executed paths only
        # so the JSON ``tool_calls`` metric reflects real work.
        if ev_type != EventType.TOOL_COMPLETE:
            return
        data = getattr(event, "data", None) or {}
        if data.get("status") == "cancelled":
            return
        tool_calls_count += 1

    transport_unsubscribe = transport.subscribe(_on_tool_event)

    final_text = ""
    runtime_error: Optional[BaseException] = None
    try:
        final_text = agent.chat(
            spec.prompt or "",
            max_iterations=max_iterations,
            cancellation_token=token,
        )
    except Exception as exc:
        runtime_error = exc
    finally:
        agent.remove_event_observer(_on_event)
        transport_unsubscribe()
        _restore_signal_handlers(prev_sigint, prev_sigterm)

    if replay_path is None and agent.replay_manager is not None:
        recorder = agent.replay_manager.recorder
        if recorder is not None:
            replay_path = str(recorder.path)

    error, exit_code, status = _classify_outcome(
        transport=transport,
        token=token,
        runtime_error=runtime_error,
        max_iterations=max_iterations,
    )

    delta_prompt = max(0, agent.llm.total_prompt_tokens - pre_prompt)
    delta_completion = max(0, agent.llm.total_completion_tokens - pre_completion)
    usage = RunUsage(
        prompt_tokens=delta_prompt,
        completion_tokens=delta_completion,
        total_tokens=delta_prompt + delta_completion,
    )

    result = RunResult(
        status=status,
        session_id=agent._session_id or "",
        # Prefer the snapshot taken on TURN_BEGIN — ``run_turn`` clears
        # ``agent._current_turn_id`` in its finally block, so reading it
        # here would always serialize ``null``.
        turn_id=captured_turn_id or agent._current_turn_id,
        cwd=str(cwd),
        model=agent.llm.model,
        final_text=final_text if status == "ok" else None,
        error=error,
        replay_path=replay_path,
        usage=usage,
        tool_calls=tool_calls_count,
        warnings=warnings,
    )

    _emit(result, output_format)
    dispatch_plugin_session_end(agent, agent._session_id or "")
    try:
        agent.close()
    except Exception:
        pass
    return exit_code


def _classify_outcome(
    *,
    transport,
    token,
    runtime_error: Optional[BaseException],
    max_iterations: int,
) -> Tuple[Optional[RunErrorEnvelope], int, str]:
    """Map post-chat state to ``(error_envelope, exit_code, status)``."""
    if transport.rejection is not None:
        return (
            RunErrorEnvelope(**transport.rejection),
            EXIT_PERMISSION_OR_INTERACTION,
            "error",
        )
    if transport.max_iterations_hit:
        return (
            RunErrorEnvelope(
                type="max_iterations",
                message=(
                    f"reached max tool call iterations ({max_iterations}); "
                    "response may be incomplete."
                ),
            ),
            EXIT_MAX_ITERATIONS,
            "error",
        )
    if token.is_cancelled:
        # SIGINT path. ``runtime/turn.py`` already swallowed the
        # KeyboardInterrupt and returned sentinel text.
        return (
            RunErrorEnvelope(
                type="interrupted",
                message=f"interrupted ({token.reason or 'sigint'})",
            ),
            EXIT_INTERRUPTED,
            "error",
        )
    if runtime_error is not None:
        return (
            RunErrorEnvelope(type="runtime_error", message=str(runtime_error)),
            EXIT_RUNTIME_ERROR,
            "error",
        )
    return None, EXIT_OK, "ok"


def _install_signal_handlers(token) -> Tuple[Any, Any]:
    """Route SIGINT/SIGTERM through ``token.cancel("sigint")``.

    Returns the previous handlers so :func:`_restore_signal_handlers`
    can put them back. ``(None, None)`` is returned when the install
    fails (not on the main thread); the restore is then a no-op.
    """
    def _on_signal(signum, frame):  # noqa: ARG001
        token.cancel("sigint")

    try:
        prev_sigint = signal.signal(signal.SIGINT, _on_signal)
        prev_sigterm = signal.signal(signal.SIGTERM, _on_signal)
        return prev_sigint, prev_sigterm
    except (ValueError, OSError):
        return None, None


def _restore_signal_handlers(prev_sigint, prev_sigterm) -> None:
    if prev_sigint is None and prev_sigterm is None:
        return
    try:
        if prev_sigint is not None:
            signal.signal(signal.SIGINT, prev_sigint)
        if prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)
    except (ValueError, OSError):
        pass


__all__ = [
    "DEFAULT_MAX_ITERATIONS",
    "EXIT_INTERRUPTED",
    "EXIT_INVALID_USAGE",
    "EXIT_MAX_ITERATIONS",
    "EXIT_OK",
    "EXIT_PERMISSION_OR_INTERACTION",
    "EXIT_RUNTIME_ERROR",
    "_execute_with_args",
    "add_run_subparser",
    "execute",
]
