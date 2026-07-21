"""AgentaoCLI — the interactive CLI class (slim core).

The class itself holds session state (``current_session_id``,
``current_mode``, ``_acp_manager`` etc.) and wires the agent, plan
controller, permission engine, and prompt session together.

Everything else — display, input loop, status bar, ACP routing, slash
commands, transport and session lifecycle — lives in sibling modules
and is delegated to here.  External callers (``entrypoints``, ``plan
controller``, ``commands``, tests) continue to call ``AgentaoCLI``
methods, which now forward to the extracted helpers.
"""

from __future__ import annotations

import functools
import json
import os
import uuid as _uuid_mod
from pathlib import Path
from typing import Callable, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from .._env import safe_load_dotenv

from ..agent import Agentao
from ..paths import user_root
from .display import DisplayController
from ..embedding import build_from_environment
from ..embedding.factory import resolve_provider_name
from ..transport import AgentEvent
from ._globals import console
from ._utils import _SlashCompleter


AgentFactory = Callable[..., Agentao]
"""Signature: ``(transport, max_context_tokens, plan_session) -> Agentao``.

All three are passed as keyword arguments and are **CLI-owned**: the transport
*is* the ``AgentaoCLI`` instance, and the plan session is the object the CLI's
``PlanController`` drives. A factory must forward them to the runtime rather
than substituting its own.

``transport`` and ``plan_session`` are enforced by
:func:`_check_agent_postconditions`. ``max_context_tokens`` is **not** — the
runtime folds it into a context manager rather than storing it verbatim, so
there is nothing cheap to compare against.

Distinct from ``agentao.acp.session_new.AgentFactory``, which has an
incompatible signature (``cwd``, ``client_capabilities``, ``transport``,
``permission_engine``, ``mcp_servers``, ``model``). Kept module-internal for
now so the two names cannot be confused at an import site; see
``docs/design/cli-host-agent-factory.md`` §11 Q1.
"""


# Attributes ``AgentaoCLI`` and its command handlers bind off the returned
# runtime. Checked up-front so a non-conforming factory fails with one pointed
# error instead of an unrelated AttributeError several steps — or several slash
# commands — later. Design: ``docs/design/cli-host-agent-factory.md`` §3.1.
#
# Bound during __init__:
_REQUIRED_AGENT_ATTRS = (
    "transport",
    "working_directory",
    "permission_engine",
    "tools",
    "tool_runner",
    # Driven by the input loop and slash commands (/clear, /new, /sessions,
    # /replay, /model). Listed here so a runtime missing them fails at startup
    # rather than mid-session, after on_session_end() has already fired.
    "messages",
    "memory_manager",
    "context_manager",
    "skill_manager",
    "clear_history",
    "get_current_model",
)

# CLI-owned kwargs a factory must not pre-bind — see _reject_prebound_kwargs.
_CLI_OWNED_KWARGS = frozenset({"transport", "max_context_tokens", "plan_session"})

_FACTORY_DOC = "docs/design/cli-host-agent-factory.md"


def _transport_chain(transport, *, limit: int = 16):
    """Yield ``transport`` and each transport it wraps, outermost first.

    Agentao's own decorator convention exposes the wrapped transport as
    ``inner`` (``agentao.replay.adapter.ReplayAdapter.inner``); ``_inner`` is
    accepted as the private spelling. ``limit`` bounds a pathological or cyclic
    chain rather than hanging startup.
    """
    seen = []
    current = transport
    for _ in range(limit):
        if current is None or any(current is s for s in seen):
            return
        seen.append(current)
        yield current
        current = getattr(current, "inner", None) or getattr(current, "_inner", None)


def _reject_prebound_kwargs(factory) -> None:
    """Reject a ``functools.partial`` that pre-binds a CLI-owned kwarg.

    ``partial(f, transport=host)`` does **not** win over the CLI's call-time
    ``transport=self`` — Python resolves ``partial(f, k=v)(k=w)`` to ``w``. So
    the host's value is silently discarded, the runtime is correctly bound to
    the CLI, and every post-condition below passes. Nothing downstream can
    detect it, which is why it has to be caught here, before the call.

    Only ``functools.partial`` is inspected because that is the shape the API
    documents and recommends. A hand-written wrapper that hard-codes one of
    these is caught later by the post-conditions (for ``transport`` and
    ``plan_session``) or not at all (``max_context_tokens``).
    """
    keywords = getattr(factory, "keywords", None)
    if not isinstance(factory, functools.partial) or not keywords:
        return
    clash = sorted(_CLI_OWNED_KWARGS & set(keywords))
    if clash:
        raise TypeError(
            f"agent_factory pre-binds CLI-owned keyword(s): {', '.join(clash)}. "
            "functools.partial keywords are overridden by the CLI's call-time "
            "values, so these would be silently discarded rather than applied. "
            f"Remove them from the partial (see {_FACTORY_DOC} §3.2)"
        )


def _check_agent_postconditions(agent, cli: "AgentaoCLI") -> None:
    """Validate the runtime returned by an ``agent_factory``.

    The CLI cannot operate on an arbitrary object: it binds ``working_directory``
    and ``permission_engine`` off the agent, registers the plan tools into
    ``agent.tools``, and drives ``agent.tool_runner``. A factory that omits any
    of those produces an error far from its cause — most sharply a bare
    ``AttributeError: 'NoneType' object has no attribute 'set_mode'`` when
    ``permission_engine`` is left at its ``None`` default.

    **Transport (§3.2).** The CLI must be *reachable* from the runtime's
    transport — either directly, or through a chain of wrappers exposing
    ``inner``. Reachability rather than identity because wrapping is agentao's
    own convention: ``ReplayManager.start()`` sets
    ``agent.transport = ReplayAdapter(agent.transport, recorder)``, so a factory
    that enables recording before returning is legitimate and an ``is``
    comparison would reject it. What is rejected is a transport with no path
    back to the CLI at all — that runtime's streaming output, permission
    prompts, and events never reach the terminal, and the CLI simply appears to
    hang.

    ``tool_runner._transport`` is checked separately: ``ToolRunner`` captures
    the transport at construction (``runtime/tool_runner.py``) and routes
    permission prompts through its own copy, so a runtime whose two transport
    fields disagree hangs at the first confirmation even though
    ``agent.transport`` looks correct.

    **Limits worth knowing.** These are ``hasattr``/``is`` probes, not proof of
    a working runtime. A ``Mock`` or a ``__getattr__`` proxy satisfies every
    attribute probe vacuously; ``_session_id`` assignability is not checked at
    all, because a proxy that stores the write on itself accepts it silently
    (the design doc records the resulting symptom: events carry the runtime's
    construction-time UUID instead of the CLI session id). Making these
    airtight would require ``isinstance(agent, Agentao)``, which would also
    forbid the host wrappers this seam exists to serve — see
    ``docs/design/cli-host-agent-factory.md`` §11 Q6.

    Raises:
        TypeError: with a message naming the specific violated post-condition.
    """
    where = f"see {_FACTORY_DOC} §3.1"

    if agent is None:
        raise TypeError(
            f"agent_factory returned None; it must return an Agentao runtime ({where})"
        )

    missing = [name for name in _REQUIRED_AGENT_ATTRS if not hasattr(agent, name)]
    if missing:
        raise TypeError(
            f"agent_factory returned {type(agent).__name__}, which is missing "
            f"required attribute(s): {', '.join(missing)}. The interactive CLI "
            f"needs a real Agentao runtime ({where})"
        )

    for label, transport in (
        ("transport", agent.transport),
        ("tool_runner._transport", getattr(agent.tool_runner, "_transport", None)),
    ):
        if not any(node is cli for node in _transport_chain(transport)):
            raise TypeError(
                f"agent_factory returned a runtime whose {label} does not reach "
                "the CLI. transport= is CLI-owned: forward it unchanged, or wrap "
                "it in an adapter exposing the wrapped transport as `inner`. "
                "Otherwise streaming output, permission prompts, and events never "
                f"reach the terminal and the CLI appears to hang (see {_FACTORY_DOC} §3.2)"
            )

    if agent.permission_engine is None:
        raise TypeError(
            "agent_factory returned a runtime with permission_engine=None. The "
            "CLI drives permission modes through it; build_from_environment() "
            "supplies one automatically, so pass permission_engine= explicitly "
            f"if constructing Agentao() directly ({where})"
        )

    agent_plan_session = getattr(agent, "_plan_session", None)
    if agent_plan_session is not cli._plan_session:
        raise TypeError(
            "agent_factory returned a runtime bound to a different PlanSession. "
            "plan_session= is CLI-owned and must be forwarded unchanged — "
            "otherwise /plan switches the CLI into plan mode while the runtime "
            "stays out of it, hiding plan_save/plan_finalize from the model and "
            f"leaving no way to finish the plan ({where})"
        )


class AgentaoCLI:
    """CLI interface for Agentao."""

    def __init__(self, *, agent_factory: Optional[AgentFactory] = None):
        """Initialize CLI.

        Args:
            agent_factory: Optional host-supplied runtime builder, called as
                ``factory(transport=self, max_context_tokens=..., plan_session=...)``.
                Lets a host embed the stock interactive CLI while supplying
                ``extra_tools`` / ``filesystem`` / ``shell`` / any other
                ``Agentao`` constructor contract, normally via
                ``functools.partial(build_from_environment, ...)``. ``None``
                (default) takes the exact existing ``build_from_environment``
                path. The returned runtime is validated by
                :func:`_check_agent_postconditions`.

                Caveat — ``llm_client=``: an injected client is used by the
                main runtime but **not** inherited by sub-agents.
                ``AgentToolWrapper`` re-resolves the LLM from the raw
                ``api_key``/``base_url``/``model`` scalars and builds a stock
                client, so ``/agent <name> <task>`` bypasses a host's proxy /
                auth / instrumentation, and a duck-typed client without an
                ``api_key`` attribute raises there. Pre-existing behavior, not
                introduced by this seam; do not advertise ``llm_client=``
                through the CLI as fully supported until that is fixed.

                The three kwargs above must not be pre-bound into a
                ``functools.partial`` — see :func:`_reject_prebound_kwargs`.
        """
        safe_load_dotenv()

        # Provisional project root for ``.agentao/`` lookups, mirroring the
        # factory's ``(working_directory or Path.cwd()).expanduser().resolve()``.
        # Re-bound below to ``self.agent.working_directory`` — the authoritative
        # frozen root — once the agent is built. The two used to be guaranteed
        # equal because the CLI passed no ``working_directory``; ``agent_factory``
        # invalidated that, so anything read from disk before the factory runs
        # must be re-read afterwards if the root moved (see ``_saved`` below).
        self._project_root: Path = Path.cwd().expanduser().resolve()

        self.current_session_id: Optional[str] = str(_uuid_mod.uuid4())
        self.current_status = None
        self._streaming_output = False
        self.markdown_mode = True
        self.last_response: str | None = None
        # Images staged via /image, attached to (and consumed by) the next
        # chat turn. Each entry is {"data": <base64>, "mimeType": ...}.
        self._staged_images: list = []
        from ..plan import PlanSession, PlanController
        self._plan_session = PlanSession()
        self._plan_controller: Optional[object] = None
        provider = resolve_provider_name()
        self.current_provider = provider

        context_limit = int(os.getenv("AGENTAO_CONTEXT_TOKENS", "200000"))

        self.allow_all_tools = False
        self.readonly_mode = False
        self._cached_ctx_pct: float = 0.0
        self._streaming_started: bool = False

        from ..permissions import PermissionMode as _PM

        def _load_saved_mode() -> "_PM":
            saved = self._load_settings().get("mode", "workspace-write")
            try:
                return _PM(saved)
            except ValueError:
                return _PM.WORKSPACE_WRITE

        # Provisional: the authoritative read happens after the factory returns,
        # once ``_project_root`` is known. Set now so ``current_mode`` exists for
        # anything the factory triggers through ``transport=self``.
        self.current_mode: _PM = _load_saved_mode()

        # Resolved here rather than in the signature default so the default
        # stays explicit and a replaceable callable is not captured in a
        # default argument. Patching ``agentao.cli.app.build_from_environment``
        # is not a supported seam — pass ``agent_factory=`` instead.
        factory = build_from_environment if agent_factory is None else agent_factory
        _reject_prebound_kwargs(factory)
        self.agent = factory(
            transport=self,
            max_context_tokens=context_limit,
            plan_session=self._plan_session,
        )
        # Runs on the default path too: the checks are cheap probes, and keeping
        # them unconditional makes the default path a live regression test for
        # the contract host factories are held to.
        _check_agent_postconditions(self.agent, self)
        # Re-bind to the agent's frozen working_directory — the authoritative
        # resolved root for ``.agentao/`` reads/writes.
        _provisional_root = self._project_root
        self._project_root = self.agent.working_directory
        if self._project_root != _provisional_root:
            # A factory supplied its own ``working_directory=``, so the mode
            # read above came from the wrong ``settings.json``. Re-read before
            # anything applies it: otherwise the project's saved posture is
            # ignored at startup and then overwritten on the next ``/mode``.
            self.current_mode = _load_saved_mode()
        # Hold a reference so the CLI can switch modes / inspect rules
        # without going through the agent.
        self.permission_engine = self.agent.permission_engine

        from ..plan import PlanController
        self._plan_controller = PlanController(
            session=self._plan_session,
            permission_engine=self.permission_engine,
            apply_mode_fn=self._apply_mode,
            load_settings_fn=self._load_settings,
        )
        from ..tools.plan import PlanSaveTool, PlanFinalizeTool
        self.agent.tools.register(PlanSaveTool(self._plan_controller))
        self.agent.tools.register(PlanFinalizeTool(self._plan_controller))

        self.agent._session_id = self.current_session_id
        self.agent.tool_runner._session_id = self.current_session_id

        from .subcommands import _load_and_register_plugins
        _load_and_register_plugins(self.agent)

        _kb = KeyBindings()

        @_kb.add('enter')
        def _pt_submit(event):
            event.current_buffer.validate_and_handle()

        @_kb.add('escape', 'enter')
        def _pt_newline(event):
            event.current_buffer.insert_text('\n')

        _history_file = str(user_root() / "history")
        os.makedirs(os.path.dirname(_history_file), exist_ok=True)
        self._prompt_session = PromptSession(
            history=FileHistory(_history_file),
            key_bindings=_kb,
            multiline=True,
            prompt_continuation='',
            completer=_SlashCompleter(),
            bottom_toolbar=self._get_status_toolbar,
            style=Style.from_dict({"bottom-toolbar": "noreverse bg:default"}),
        )

        self.display = DisplayController(console, lambda: self.current_status)

        # ACP client manager — lazy-initialized on first use or /acp command.
        self._acp_manager = None
        self._acp_load_error_shown = False
        self._acp_config_mtime: Optional[float] = None

        self.permission_engine.set_mode(self.current_mode)
        from ..permissions import PermissionMode as _PM2
        self.readonly_mode = (self.current_mode == _PM2.READ_ONLY)
        self._apply_readonly_mode()

    # ── Settings management ─────────────────────────────────────────────

    def _apply_readonly_mode(self) -> None:
        self.agent.tool_runner.set_readonly_mode(self.readonly_mode)

    def _load_settings(self) -> dict:
        from ..replay.config import settings_path
        path = settings_path(self._project_root)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _save_settings(self) -> None:
        from ..replay.config import settings_path
        path = settings_path(self._project_root)
        path.parent.mkdir(exist_ok=True)
        data = self._load_settings()
        data["mode"] = self.current_mode.value
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _apply_mode(self, mode) -> None:
        previous_mode = self.permission_engine.active_mode
        self.current_mode = mode
        self.permission_engine.set_mode(mode)
        from ..permissions import PermissionMode
        self.readonly_mode = (mode == PermissionMode.READ_ONLY)
        self._apply_readonly_mode()
        self.allow_all_tools = False
        self._save_settings()
        # Step 6 replay event — surface the user-visible mode transition.
        if previous_mode != mode:
            try:
                from ..transport import AgentEvent, EventType
                self.agent.transport.emit(AgentEvent(
                    EventType.PERMISSION_MODE_CHANGED,
                    {
                        "previous": getattr(previous_mode, "value", str(previous_mode)),
                        "current": getattr(mode, "value", str(mode)),
                        "cause": "cli",
                    },
                ))
            except Exception:
                pass

    # ── ACP inbox flush (delegated) ─────────────────────────────────────

    def _try_acp_explicit_route(self, user_input: str) -> bool:
        from .acp_inbox import try_acp_explicit_route
        return try_acp_explicit_route(self, user_input)

    def _flush_acp_inbox(self) -> None:
        from .acp_inbox import flush_acp_inbox
        flush_acp_inbox(self)

    # ── Transport protocol delegation ───────────────────────────────────

    def emit(self, event: AgentEvent) -> None:
        from .transport import emit_event
        emit_event(self, event)

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        from .transport import confirm_tool_execution
        return confirm_tool_execution(self, tool_name, description, args)

    def confirm_tool_execution(self, tool_name: str, tool_description: str, tool_args: dict) -> bool:
        from .transport import confirm_tool_execution
        return confirm_tool_execution(self, tool_name, tool_description, tool_args)

    def on_llm_thinking(self, reasoning: str) -> None:
        from .transport import on_llm_thinking
        on_llm_thinking(self, reasoning)

    def on_max_iterations(self, max_iterations: int, pending_tools: list) -> dict:
        from .transport import on_max_iterations
        return on_max_iterations(self, max_iterations, pending_tools)

    def on_llm_text(self, chunk: str) -> None:
        from .transport import on_llm_text
        on_llm_text(self, chunk)

    def ask_user(
        self,
        question: str,
        *,
        header=None,
        options=None,
        multiple: bool = False,
        allow_custom: bool = True,
    ) -> str:
        from .transport import ask_user
        return ask_user(
            self,
            question,
            header=header,
            options=options,
            multiple=multiple,
            allow_custom=allow_custom,
        )

    # ── Session lifecycle delegation ────────────────────────────────────

    def on_session_start(self) -> None:
        from .session import on_session_start
        on_session_start(self)

    def on_session_end(self) -> None:
        from .session import on_session_end
        on_session_end(self)

    def _save_session_on_exit(self):
        self.on_session_end()

    # ── Display (delegated) ─────────────────────────────────────────────

    def print_welcome(self):
        from .ui import print_welcome
        print_welcome(self)

    def print_help(self):
        from .ui import print_help
        print_help(self)

    def list_skills(self):
        from .ui import list_skills
        list_skills(self)

    def show_status(self):
        from .ui import show_status
        show_status(self)

    # ── Input / status bar (delegated) ──────────────────────────────────

    def _get_user_input(self) -> str:
        from .input_loop import get_user_input
        return get_user_input(self)

    def _get_status_toolbar(self) -> ANSI:
        from .input_loop import get_status_toolbar
        return get_status_toolbar(self)

    # ── Main loop ───────────────────────────────────────────────────────

    def run(self):
        self.print_welcome()
        try:
            self._run_loop()
        finally:
            # Stop ACP server subprocesses before closing the agent.
            if self._acp_manager is not None:
                try:
                    self._acp_manager.stop_all()
                except Exception:
                    pass
            self.agent.close()

    def _run_loop(self):
        from .input_loop import run_loop
        run_loop(self)
