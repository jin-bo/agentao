"""Main agent logic for Agentao."""

import asyncio
import logging
import os
import concurrent.futures
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, Iterable, List, Optional, Sequence, Set, TypeVar, Union, TYPE_CHECKING

from .llm import LLMClient
from .llm.client import KEEP_BASE_URL as _KEEP_BASE_URL
from .permissions import (
    PermissionEngine,
    PermissionMode,
    _parse_construction_permission_mode,
)
from .runtime import ChatLoopRunner, ToolRunner, run_llm_call, run_turn
from .runtime import model as _runtime_model
from .runtime import permission_mode as _runtime_permission_mode
from .runtime.tool_executor import ASYNC_CANCEL_REASON
from .tools import ToolRegistry, SaveMemoryTool, TodoWriteTool
from .tooling import (
    apply_enabled_tools,
    init_mcp,
    register_agent_tools,
    register_builtin_tools,
    register_extra_tools,
    register_mcp_tools,
)
from .agents import AgentManager
from .cancellation import CancellationToken
from .plan import PlanSession
from .prompts import (
    SystemPromptBuilder,
    extract_context_hints,
    load_project_instructions,
)
from .skills import SkillManager
from .context_manager import ContextManager
from .sandbox import SandboxPolicy
from .transport import NullTransport

if TYPE_CHECKING:
    from .compaction.coordinator import CompactionCoordinator
    from .compaction.types import CompactionController, CompactionOutcome, ManualCompactionReason
    from .agents.bg_store import BackgroundTaskStore  # noqa: F401
    from .capabilities import FileSystem, MCPRegistry, ShellExecutor
    from .mcp import McpClientManager  # type-only; MCP SDK is heavy
    from .memory import MemoryManager
    from .replay import ReplayConfig, ReplayManager  # type-only — replay no longer in core surface
    from .host.models import ActivePermissions, HostEvent
    from .host.stream import TextDelta  # noqa: F401
    from .outcome import TurnOutcome  # noqa: F401
    from .tools.base import RegistrableTool  # noqa: F401
    from .transport.base import CoreTransport
    from types import TracebackType


_logger = logging.getLogger(__name__)

# The observer methods hand back the callback they were given, with its own type.
_ObserverT = TypeVar("_ObserverT", bound="Callable[[HostEvent], object]")


def _log_abandoned_close_failure(work: "concurrent.futures.Future[None]") -> None:
    exc = work.exception()
    if exc is not None:
        _logger.warning(
            "close() failed after aclose() was cancelled",
            exc_info=(type(exc), exc, exc.__traceback__),
        )


# ``with`` / ``async with`` hand back the agent with its own (sub)class type.
_AgentT = TypeVar("_AgentT", bound="Agentao")


#: Workers for the ``arun`` bridge pool. Deliberately the same capacity
#: ``asyncio`` gives its own default executor, so moving off that executor costs
#: no concurrency.
_ARUN_POOL_MAX_WORKERS = min(32, (os.cpu_count() or 1) + 4)

_arun_pool: Optional[ThreadPoolExecutor] = None
_arun_pool_lock = threading.Lock()


def _get_arun_pool() -> ThreadPoolExecutor:
    """The pool :meth:`Agentao.arun` runs ``chat()`` on.

    **Not the loop's default executor**, which is what ``run_in_executor(None,
    ...)`` used to mean here. A turn holds its worker for the whole turn, and
    partway through it blocks in ``tool_executor._run_async_tool`` waiting on a
    tool coroutine running on the host loop. So once concurrent turns reach the
    pool's worker count, anything on that loop needing a default-executor worker
    can never get one — and the turns are waiting on precisely that work.

    That includes code agentao does not control. ``loop.getaddrinfo`` submits
    ``socket.getaddrinfo`` to the default executor, and every
    ``httpx.AsyncClient`` connect to a hostname goes through it (measured: one
    ``run_in_executor`` call against the default executor per request, resolving
    on an ``asyncio_N`` thread). An async tool doing an ordinary HTTPS fetch
    would therefore hang, with no way to redirect the lookup from our side.

    Shared and lazily built: a host that only ever calls the sync ``chat()``
    never pays for the threads, and N ``Agentao`` instances do not mean N pools.
    """
    global _arun_pool
    with _arun_pool_lock:
        if _arun_pool is None:
            _arun_pool = ThreadPoolExecutor(
                max_workers=_ARUN_POOL_MAX_WORKERS,
                thread_name_prefix="agentao-arun",
            )
        return _arun_pool


#: How long a cancelled ``arun`` waits for its worker to finish the turn
#: (orphaned-tool backfill, TURN_END) before re-raising. Matches the async-tool
#: cancel ack budget: past it the worker is stuck in something that does not
#: poll the token, and the turn lock keeps the next turn from overlapping it.
_ARUN_CANCEL_CLEANUP_TIMEOUT_S = 5.0


async def _await_turn_cleanup(
    future: "asyncio.Future[str]", work: "concurrent.futures.Future[str]",
) -> None:
    """Wait, bounded, for a cancelled turn's worker to finish.

    ``asyncio.wait`` never cancels what it waits on, so a timeout leaves the
    worker running. Its outcome is then logged from ``work`` — the executor's
    own future — because the host may close its loop as soon as it has its
    ``CancelledError`` (``asyncio.run`` does), and a result arriving after
    that never reaches the loop-bound ``future``. The host's own
    ``CancelledError`` is what the caller re-raises either way.
    """
    try:
        done, _ = await asyncio.wait({future}, timeout=_ARUN_CANCEL_CLEANUP_TIMEOUT_S)
    except asyncio.CancelledError:
        # Cancelled again while waiting: stop waiting, keep the first cancel.
        done = set()
    # ``future`` holds its own copy of any exception; reading it here (or when
    # it lands, if the loop is still open) keeps asyncio from also reporting
    # "Future exception was never retrieved" for an error already logged.
    if future in done:
        _retrieve(future)
        _log_turn_cleanup_outcome(work)
    else:
        _logger.warning(
            "arun cancelled; the turn did not finish within %.0fs and is still "
            "running in the background", _ARUN_CANCEL_CLEANUP_TIMEOUT_S,
        )
        future.add_done_callback(_retrieve)
        work.add_done_callback(_log_turn_cleanup_outcome)


def _retrieve(future: "asyncio.Future[str]") -> None:
    if not future.cancelled():
        future.exception()


def _log_turn_cleanup_outcome(work: "concurrent.futures.Future[str]") -> None:
    if work.cancelled():
        return
    exc = work.exception()
    if exc is not None:
        _logger.warning("cancelled turn raised during cleanup: %r", exc)


class Agentao:
    """Agentao agent with tool, skill, and MCP support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        # Everything below is KEYWORD-ONLY. Until 0.5.0 eight legacy callbacks
        # sat interleaved with the next four parameters, positionally; with
        # them gone, a caller still passing
        # ``Agentao(key, url, model, temp, max_tok, confirmation_cb)`` would
        # have bound the callback to ``max_context_tokens`` without a word.
        # Behind the ``*`` that call is a ``TypeError`` at the call site.
        *,
        max_context_tokens: int = 200_000,
        permission_engine: Optional[PermissionEngine] = None,
        # Build an engine in this mode (``"read-only"`` /
        # ``"workspace-write"`` / ``"full-access"``; ``"plan"`` is refused).
        # ``None`` builds none. Mutually exclusive with ``permission_engine=``.
        permission_mode: Optional[Union[str, PermissionMode]] = None,
        transport: Optional["CoreTransport"] = None,  # CoreTransport protocol instance
        plan_session: Optional[PlanSession] = None,
        working_directory: Path,
        # Host LLM request-body passthrough. Same raw-config family as
        # api_key/.../max_tokens above; mutually exclusive with llm_client=
        # (see _validate_construction_args).
        extra_body: Optional[Dict[str, Any]] = None,
        # Explicit prompt-cache breakpoints for this endpoint (stage 0b).
        # KEYWORD-ONLY for the same reason as ``extra_body``, and in the same
        # raw-config family: mutually exclusive with ``llm_client=``.
        prompt_cache: Optional[str] = None,
        prompt_cache_ttl: Optional[str] = None,
        # The wire protocol spoken to ``base_url``. Same raw-config family,
        # KEYWORD-ONLY for the same reason.
        api_format: Optional[str] = None,
        extra_mcp_servers: Optional[Dict[str, Dict[str, Any]]] = None,
        # Host compaction control plane. KEYWORD-ONLY for the same reason as
        # ``extra_body`` above — inserting it into the older group would shift
        # the legacy positional callback arguments. At most one: this is not a
        # list, and it is consulted only after every command hook has allowed.
        compaction_controller: Optional["CompactionController"] = None,
        # ── Host tool injection ───────────────────────────────────────────
        extra_tools: Optional[Sequence["RegistrableTool"]] = None,
        disable_tools: Optional[Iterable[str]] = None,
        enabled_tools: Optional[Iterable[str]] = None,
        # Embedded-harness explicit-injection kwargs.
        llm_client: Optional[LLMClient] = None,
        logger: Optional[logging.Logger] = None,
        memory_manager: Optional["MemoryManager"] = None,
        skill_manager: Optional[SkillManager] = None,
        project_instructions: Optional[str] = None,
        mcp_manager: Optional["McpClientManager"] = None,
        mcp_registry: Optional["MCPRegistry"] = None,
        filesystem: Optional["FileSystem"] = None,
        shell: Optional["ShellExecutor"] = None,
        # Opt-in subsystems — ``None`` (default) disables. The factory
        # wires CLI defaults from ``<wd>/.agentao/*``.
        bg_store: Optional["BackgroundTaskStore"] = None,
        sandbox_policy: Optional[SandboxPolicy] = None,
        replay_config: Optional["ReplayConfig"] = None,
        enable_builtin_agents: bool = False,
    ) -> None:
        """Initialize Agentao agent.

        Args:
            api_key: API key for LLM service.
            base_url: Base URL for API endpoint.
            model: Model name to use.
            prompt_cache: Explicit prompt-cache breakpoint format for this
                endpoint — ``"anthropic"`` or ``None``/``"off"`` (default off).
                Raw-config only: a host that injects ``llm_client=`` passes it
                to that client instead.
            prompt_cache_ttl: Retention hint, ``"5m"`` (provider default) or
                ``"1h"``. Ignored when ``prompt_cache`` is off.
            api_format: The wire protocol spoken to ``base_url`` —
                ``"openai-completions"`` (default), ``"anthropic-messages"``
                (Anthropic's Messages API, over the official SDK) or
                ``"openai-responses"`` (the OpenAI Responses API). Configured,
                never inferred from the URL or the model name; only
                ``set_provider(api_format=)`` changes it afterwards, and
                sub-agents are built on the current one. Raw-config only:
                a host that injects ``llm_client=`` passes it to that client
                instead. An unknown value raises ``ValueError``.
            extra_body: Optional dict forwarded verbatim to the LLM
                ``.create()`` call as the SDK's ``extra_body`` request
                option (``reasoning_effort`` / ``top_p`` / ``seed`` /
                ``response_format`` / any provider-specific field). Only
                valid on the raw-config path; mutually exclusive with
                ``llm_client=`` (a host injecting its own client passes
                ``extra_body=`` to that client directly). See
                ``docs/design/host-llm-extra-params.md``.
            transport: A CoreTransport instance that receives all runtime events and
                       handles interactive requests (confirm_tool, ask_user, etc.).
                       ``subscribe()`` is optional; only ``astream()`` needs it.
                       If omitted, a NullTransport is used (silent /
                       headless mode: every confirmation is approved).
            max_context_tokens: Maximum context window tokens (default 200K).
            permission_engine: Optional PermissionEngine for rule-based tool access.
            permission_mode: ``"read-only"`` / ``"workspace-write"`` /
                ``"full-access"`` (or the matching ``PermissionMode``);
                ``"plan"`` is refused — plan mode is entered through the
                plan session. Builds ``PermissionEngine(project_root=
                working_directory, rules=[])`` — no permission file is read —
                and starts the agent in that mode with both read-only
                switches set and **no event emitted** (a starting state is
                not a switch). ``None`` (default) builds no engine. Mutually
                exclusive with ``permission_engine=``.
            working_directory: Per-runtime working directory (required
                since 0.3.0; was a deprecated optional in 0.2.16).
                Frozen at construction: memory/permissions/MCP config/
                AGENTAO.md/system-prompt rendering/file tools/shell tool
                all resolve against it. Two Agentao instances created
                with different ``working_directory`` values can coexist
                in the same process. Use
                :func:`agentao.embedding.build_from_environment` for
                CLI-style auto-detection from the surrounding cwd /
                ``.env`` / ``.agentao/`` files.
            extra_mcp_servers: Optional in-memory MCP server configs to merge
                **on top of** the file-loaded ``.agentao/mcp.json``. Used by
                ACP ``session/new`` (Issue 11) to inject session-scoped
                servers without writing to the project's config files.
                Already in Agentao's internal dict shape — translation from
                ACP wire format lives in
                :func:`agentao.acp.mcp_translate.translate_acp_mcp_servers`.
                Per-name override semantics: an entry here replaces a
                file-loaded entry with the same name. ``None`` means "no
                extras", which is the CLI default and produces the legacy
                file-only behavior.
            extra_tools: Pre-built ``Tool`` / ``AsyncToolBase`` instances to
                register on top of the built-ins. Registered as the final
                pass (after built-in, MCP, and agent tools), so a same-named
                entry overrides a built-in or agent tool. Names using the
                reserved ``mcp_`` prefix, or duplicate names, raise at
                construction. Injected tools inherit the same
                working-directory / filesystem / shell binding as built-ins.
                See ``docs/design/host-tool-injection.md``.
            disable_tools: Built-in tool names to skip registering (e.g.
                ``{"run_shell_command"}`` for a read-only deployment). Each
                name must be a known built-in or construction raises (typo
                guard). Only skips built-in registration — not a global
                denylist and not a security boundary (that stays with the
                permission engine).
            enabled_tools: Additive allowlist of tool names. ``None`` (default)
                disables the allowlist (status quo: all built-in + agent +
                extra register). Any iterable — including the empty set —
                *enables* it: after registration, every built-in / agent-path
                tool whose name is absent is pruned. ``extra_tools``, MCP
                (``mcp_*``), and plan-only tools are left untouched. Mutually
                exclusive with ``disable_tools`` (passing both raises).
                Reserved names (``mcp_`` prefix, plan-only) raise at
                construction; unknown names raise after registration (typo
                guard). See ``docs/design/host-tool-allowlist.md``.

        The eight legacy callback kwargs (``confirmation_callback``,
        ``step_callback``, ``thinking_callback``, ``ask_user_callback``,
        ``output_callback``, ``tool_complete_callback``,
        ``llm_text_callback``, ``on_max_iterations_callback``) were removed
        in 0.5.0. Construct an :class:`agentao.transport.SdkTransport`, or
        wrap the old callbacks with
        :func:`agentao.embedding.compat.build_compat_transport`, and pass
        ``transport=``. See ``docs/migration/0.4.x-to-0.5.0.md``.
        """
        self._validate_construction_args(
            llm_client=llm_client,
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body,
            prompt_cache=prompt_cache,
            prompt_cache_ttl=prompt_cache_ttl,
            api_format=api_format,
            mcp_manager=mcp_manager,
            extra_mcp_servers=extra_mcp_servers,
            mcp_registry=mcp_registry,
            permission_engine=permission_engine,
            permission_mode=permission_mode,
        )
        # Parsed before anything is built, so a bad mode fails at the call
        # site rather than after half the runtime exists.
        initial_mode: Optional[PermissionMode] = (
            _parse_construction_permission_mode(permission_mode)
            if permission_mode is not None else None
        )

        # Freeze working directory to an absolute path. Resolved once so
        # subsequent accesses are cheap and consistent. Required since
        # 0.3.0 — calling ``Agentao()`` without ``working_directory=``
        # raises ``TypeError`` from Python's signature dispatch.
        self._working_directory: Path = (
            Path(working_directory).expanduser().resolve()
        )

        # Host tool injection (registered during ``_wire_tooling``).
        # ``extra_tools`` are pre-built instances; ``disable_tools`` skips
        # built-ins by name. Both are validated up-front so a typo or a
        # reserved-namespace collision fails at construction, not silently.
        self._extra_tools: List["RegistrableTool"] = list(extra_tools or ())
        self._disable_tools: frozenset = frozenset(disable_tools or ())
        # ``None`` = allowlist disabled; any iterable (incl. the empty set)
        # enables it. Applied as a final prune pass in ``apply_enabled_tools``.
        self._enabled_tools: Optional[frozenset] = (
            frozenset(enabled_tools) if enabled_tools is not None else None
        )
        self._validate_tool_injection()

        # When ``None``, file/search/shell tools fall back to
        # ``LocalFileSystem`` / ``LocalShellExecutor`` at first use.
        self.filesystem = filesystem
        self.shell = shell

        self._init_mcp_sources(extra_mcp_servers, mcp_registry)

        # Anchor the LLM debug log to the agent's effective working directory
        # so it always resolves to an absolute, writable path. CLI runs land it
        # at <project>/agentao.log (unchanged behavior, since working_directory
        # falls back to Path.cwd()); ACP sessions land it under the frozen,
        # client-supplied project cwd instead of the subprocess's cwd — which
        # for ACP launches is often "/" and read-only.
        self.llm = self._resolve_llm_client(
            llm_client,
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body,
            prompt_cache=prompt_cache,
            prompt_cache_ttl=prompt_cache_ttl,
            api_format=api_format,
            logger=logger,
        )
        # What this agent owns: what this constructor builds itself (and what
        # ``_adopt_memory_manager`` hands it). ``close()`` releases these and
        # only these, and so does a failure below, since the caller never
        # receives the object. Injected managers are the caller's and are
        # left alone; so is a manager a host assigns over the attribute
        # later, while the one built here is still released. The LLM client
        # is released too: it holds ``agentao.log`` open.
        self._built_llm_client: Optional[LLMClient] = (
            self.llm if llm_client is None else None
        )
        self._built_memory_manager: Optional["MemoryManager"] = None
        self._built_mcp_manager: Optional["McpClientManager"] = None
        try:
            self._init_skill_and_memory(skill_manager, memory_manager)
            self._last_user_message: str = ""
            self._stable_block_chars: int = 0  # size of last rendered <memory-stable> block
            # Ids rendered in the last <memory-stable> block. The volatile tail's
            # dynamic-recall pass excludes them; the two are built by separate
            # calls, so the set is handed over on the agent (see prompts/builder.py).
            self._stable_memory_ids: Set[str] = set()
            self.todo_tool = TodoWriteTool()
            if initial_mode is not None:
                # ``rules=[]``, never ``None``: ``None`` makes the engine run the
                # permission-file loader. ``permission_mode=`` asks for a preset,
                # not for whatever policy file this machine happens to hold.
                permission_engine = PermissionEngine(
                    project_root=self._working_directory, rules=[],
                )
            self.permission_engine = permission_engine

            # An explicit transport, or the silent headless default.
            self.transport: "CoreTransport" = (
                transport if transport is not None else NullTransport()
            )

            # Initialize context manager
            self.context_manager = ContextManager(
                llm_client=self.llm,
                memory_tool=self.memory_tool,
                max_tokens=max_context_tokens,
                memory_manager=self._memory_manager,
            )
            # Built on first use — see :attr:`compaction_coordinator`. Held
            # rather than rebuilt per call because it is where per-turn
            # compaction state lives.
            self._compaction_coordinator: Optional["CompactionCoordinator"] = None
            self.compaction_controller = compaction_controller

            # Session-scoped state (session id, host event stream, conversation
            # history, plan session, project instructions) must land before
            # ``_wire_tooling`` — the host emitters built there capture
            # ``self._host_events`` / ``self._session_id``.
            self._init_session_state(plan_session, project_instructions)
            self._init_replay(replay_config)
            self._wire_tooling(
                mcp_manager=mcp_manager,
                bg_store=bg_store,
                sandbox_policy=sandbox_policy,
                enable_builtin_agents=enable_builtin_agents,
            )
            if initial_mode is not None:
                # Both read-only switches, silently: a starting state is not a
                # switch, and an event here would reach the host's transport
                # before this constructor has returned. Needs ``tool_runner``,
                # hence after wiring.
                _runtime_permission_mode._set_initial_permission_mode(
                    self, initial_mode,
                )
        except BaseException:
            self._release_failed_construction()
            raise

    def _release_failed_construction(self) -> None:
        """Release what ``__init__`` built before it raised.

        Only the MCP manager, memory manager and LLM client this constructor
        created: an ``mcp_manager=`` / ``memory_manager=`` / ``llm_client=``
        the caller passed in is still the caller's, since it never got an
        agent to hand it to. Each release is best effort, so the
        constructor's own exception is the one that propagates.
        """
        mcp = self._built_mcp_manager
        try:
            if mcp is not None:
                try:
                    mcp.disconnect_all()
                except Exception:
                    _logger.warning(
                        "MCP disconnect after a failed construction", exc_info=True
                    )
        finally:
            # Even when a second Ctrl-C cuts the (bounded) disconnect short.
            memory = self._built_memory_manager
            try:
                if memory is not None:
                    try:
                        memory.close()
                    except Exception:
                        _logger.warning(
                            "memory close after a failed construction",
                            exc_info=True,
                        )
            finally:
                self._close_built_llm_client()

    def _adopt_memory_manager(self, manager: "MemoryManager") -> None:
        """Take ownership of ``manager``, passed in as ``memory_manager=``.

        For a caller that built the manager only for this agent and has no
        later point at which to close it — ``build_from_environment`` and
        the sub-agent factory: ``close()`` then releases it like one the
        constructor built. Call it only after construction succeeded; before
        that, a failure leaves the manager with the caller.
        """
        self._built_memory_manager = manager

    def _reinit_mcp(self) -> None:
        """Rebuild the MCP manager from the current server set (a plugin
        added servers), disconnecting the one this agent built.

        The new manager is the agent's own, so ``close()`` releases it. An
        injected ``mcp_manager=`` is left connected: it is the caller's.
        """
        old = self._built_mcp_manager
        if old is not None:
            try:
                old.disconnect_all()
            except Exception:
                _logger.warning("MCP disconnect before a rebuild", exc_info=True)
            # After the disconnect returns, as in ``close()``: an interrupted
            # one leaves the manager owned, for ``close()`` to finish.
            self._built_mcp_manager = None
            # A rebuild that raises must not leave the agent holding the
            # manager just closed.
            if self.mcp_manager is old:
                self.mcp_manager = None
        self.mcp_manager = self._init_mcp()
        self._built_mcp_manager = self.mcp_manager

    def _close_built_llm_client(self) -> None:
        """Release the ``agentao.log`` handle of an LLM client built here."""
        llm = self._built_llm_client
        if llm is not None:
            try:
                llm.close()
            except Exception:
                _logger.warning("LLM client close failed", exc_info=True)

    def _validate_construction_args(
        self,
        *,
        llm_client: Optional[LLMClient],
        api_key: Optional[str],
        base_url: Optional[str],
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        extra_body: Optional[Dict[str, Any]],
        prompt_cache: Optional[str],
        prompt_cache_ttl: Optional[str],
        api_format: Optional[str],
        mcp_manager: Optional["McpClientManager"],
        extra_mcp_servers: Optional[Dict[str, Dict[str, Any]]],
        mcp_registry: Optional["MCPRegistry"],
        permission_engine: Optional[PermissionEngine],
        permission_mode: Optional[Union[str, PermissionMode]],
    ) -> None:
        """Reject mutually-exclusive construction kwargs.

        A fully-constructed object always wins over its raw-config
        sibling; supplying both is a programmer error.
        """
        # ``extra_body`` belongs to the raw-config set: ``_resolve_llm_client``
        # returns an injected ``llm_client`` untouched, so ``extra_body`` would
        # be a SILENT no-op alongside it. The guard makes the mistake loud — a
        # host with its own client passes ``extra_body=`` to that client.
        if llm_client is not None and any(
            v is not None
            for v in (
                api_key, base_url, model, temperature, max_tokens, extra_body,
                prompt_cache, prompt_cache_ttl, api_format,
            )
        ):
            raise ValueError(
                "Agentao(): pass either llm_client= or "
                "api_key/base_url/model/temperature/max_tokens/extra_body/"
                "prompt_cache/prompt_cache_ttl/api_format, not both."
            )
        if mcp_manager is not None and extra_mcp_servers is not None:
            raise ValueError(
                "Agentao(): pass either mcp_manager= or extra_mcp_servers=, "
                "not both."
            )
        if mcp_manager is not None and mcp_registry is not None:
            raise ValueError(
                "Agentao(): pass either mcp_manager= (pre-built) or "
                "mcp_registry= (config source), not both."
            )
        # No precedence rule between the two: one builds an engine, the other
        # is one. A host with its own engine sets the mode on it.
        if permission_engine is not None and permission_mode is not None:
            raise ValueError(
                "Agentao(): pass either permission_engine= or "
                "permission_mode=, not both. Set the mode on your engine, or "
                "call set_permission_mode() after construction."
            )

    @staticmethod
    def _reject_reserved_tool_name(name: str, *, context: str) -> None:
        """Reject names in a reserved namespace, for inject *and* remove paths.

        Two namespaces are off-limits to host tool injection:

        * the ``mcp_`` prefix — owned by MCP discovery (``make_mcp_tool_name``);
          MCP tools are added/removed via ``mcp_manager=`` /
          ``extra_mcp_servers=``, never here.
        * ``_PLAN_ONLY_TOOLS`` (``plan_save`` / ``plan_finalize``) — bound to
          the plan-mode state machine and registered by the CLI after
          construction; a host must not add, replace, or remove them.

        Shared so ``add_tool`` (incl. ``replace=True``) and ``remove_tool``
        enforce the identical guard — rejecting only on remove would leave an
        ``add_tool(name="plan_save", replace=True)`` loophole.
        """
        from .tools.base import ToolRegistry

        if not isinstance(name, str):
            raise ValueError(
                f"{context}: tool name must be a string, got "
                f"{type(name).__name__}."
            )
        if name.startswith("mcp_"):
            raise ValueError(
                f"{context}: tool name '{name}' uses the reserved 'mcp_' "
                "prefix. Manage MCP tools via mcp_manager= / "
                "extra_mcp_servers= instead."
            )
        if name in ToolRegistry._PLAN_ONLY_TOOLS:
            raise ValueError(
                f"{context}: tool name '{name}' is reserved for plan mode "
                f"({sorted(ToolRegistry._PLAN_ONLY_TOOLS)}); these are bound to "
                "the plan-mode state machine and cannot be injected or removed."
            )

    def _validate_one_extra_tool(self, tool: "RegistrableTool", *, context: str) -> None:
        """Validate a single injected tool (construction *and* ``add_tool``).

        Enforces a non-empty string name and the reserved-namespace guard.
        Batch concerns (duplicate names within one ``extra_tools=``) stay in
        :meth:`_validate_tool_injection`; this checks one tool in isolation so
        the runtime ``add_tool`` path reuses the exact same rules.
        """
        name = tool.name
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"{context}: {type(tool).__name__}.name must "
                f"be a non-empty string, got {name!r}."
            )
        self._reject_reserved_tool_name(name, context=context)

    def _validate_tool_injection(self) -> None:
        """Validate ``extra_tools`` / ``disable_tools`` / ``enabled_tools``.

        Fails loudly rather than silently mis-registering:

        * ``extra_tools`` names must be unique, non-empty strings, and must
          not fall in a reserved namespace (``mcp_`` prefix or the plan-mode
          ``_PLAN_ONLY_TOOLS``) — see :meth:`_validate_one_extra_tool`. MCP
          replacement goes through ``mcp_manager=`` / ``extra_mcp_servers=``.
        * ``disable_tools`` names must each be a known built-in
          (:data:`BUILTIN_TOOL_NAMES`) — a typo (``{"web_serach"}``) raises
          instead of becoming a no-op. The check is against *static
          registration eligibility*, not live availability, so disabling
          ``web_search`` is legal even when ``[web]`` isn't installed
          (it's just a no-op then).
        * ``enabled_tools`` (when not ``None``) must be mutually exclusive
          with ``disable_tools`` and must not name a reserved tool
          (``mcp_`` prefix or plan-only). The unknown-name typo guard is
          deferred to :func:`apply_enabled_tools` (needs the live registry).
        """
        from .tooling.registry import (
            BUILTIN_TOOL_NAMES,
            MCP_RESOURCE_TOOL_NAMES,
            MCP_SKILL_TOOL_NAMES,
        )

        seen: set = set()
        for tool in self._extra_tools:
            self._validate_one_extra_tool(tool, context="Agentao(extra_tools=)")
            name = tool.name
            if name in seen:
                raise ValueError(
                    f"Agentao(extra_tools=): duplicate tool name '{name}'."
                )
            seen.add(name)

        disableable = BUILTIN_TOOL_NAMES | MCP_RESOURCE_TOOL_NAMES | MCP_SKILL_TOOL_NAMES
        unknown = sorted(self._disable_tools - disableable)
        if unknown:
            raise ValueError(
                f"Agentao(disable_tools=): unknown built-in tool name(s) "
                f"{unknown}. Only built-ins can be disabled (agent-path tools "
                f"like codebase_investigator and plan tools are not disableable). "
                f"Valid names: {sorted(disableable)}."
            )

        # ``enabled_tools`` allowlist — only the order-independent checks run
        # here. The unknown-name (typo) guard needs the live registry and so
        # runs in ``apply_enabled_tools`` after all registration: agent-path
        # tool names aren't known at construction (``_validate_tool_injection``
        # is called before ``AgentManager`` is built). See
        # ``docs/design/host-tool-allowlist.md``.
        if self._enabled_tools is not None:
            if self._disable_tools:
                raise ValueError(
                    "Agentao(enabled_tools=): mutually exclusive with "
                    "disable_tools=. Use one or the other — the allowlist "
                    "already expresses 'only these'."
                )
            for name in sorted(self._enabled_tools):
                self._reject_reserved_tool_name(
                    name, context="Agentao(enabled_tools=)"
                )

    def _init_mcp_sources(
        self,
        extra_mcp_servers: Optional[Dict[str, Dict[str, Any]]],
        mcp_registry: Optional["MCPRegistry"],
    ) -> None:
        """Stash the two non-manager MCP config inputs.

        ``extra_mcp_servers`` (Issue 11) is a session-scoped snapshot, stored
        privately so a caller can't mutate it after construction. We
        deep-copy at the dict level so a subsequent caller mutation cannot
        leak into ``_init_mcp``; ``None`` means "no extras", preserving the
        legacy CLI behavior of file-only MCP loading.

        ``mcp_registry`` (Issue #17) replaces the implicit file-load path
        inside ``init_mcp``. ``None`` means "use the legacy file source via
        ``load_mcp_config``" — the factory injects a ``FileBackedMCPRegistry``
        so the CLI/ACP path always sets this.
        """
        self._extra_mcp_servers: Dict[str, Dict[str, Any]] = (
            {name: dict(cfg) for name, cfg in extra_mcp_servers.items()}
            if extra_mcp_servers
            else {}
        )
        self._mcp_registry: Optional["MCPRegistry"] = mcp_registry

    def _init_session_state(
        self,
        plan_session: Optional[PlanSession],
        project_instructions: Optional[str],
    ) -> None:
        """Initialize construction-time session / conversation state.

        Covers plugin-hook slots (populated later by the CLI), the fallback
        session id + host event stream, the per-turn cancellation token,
        conversation history, the plan session, and project instructions.
        Must run before ``_wire_tooling`` because the host emitters built
        there capture ``self._host_events`` / ``self._session_id``.
        """
        # Plugin hook rules — populated by _load_and_register_plugins() in cli.py.
        self._plugin_hook_rules: list = []
        self._loaded_plugins: list = []

        # Construction-time UUID fallback so the public host contract
        # reports a non-empty ``session_id`` before the CLI/ACP layer
        # assigns a persisted id. Hosts overwrite this directly.
        from .runtime.identity import new_session_id as _new_sid
        self._session_id: str = _new_sid()
        from .host.events import EventStream
        self._host_events: EventStream = EventStream()
        self._current_turn_id: Optional[str] = None

        # Per-turn cancellation token (set at the start of each chat() call)
        self._current_token: Optional[CancellationToken] = None
        # Held for the whole of a turn by ``runtime.turn.run_turn``; a second
        # turn on this agent fails fast instead of sharing ``messages``.
        self._turn_lock = threading.Lock()
        # Serializes ``close()``: a cancelled ``aclose()`` leaves one running
        # on a worker thread, and a second close must not run the teardown
        # beside it. Re-entrant so a ``close()`` reached again on the same
        # thread (a signal handler during the teardown) does not deadlock;
        # ``_closing`` then makes that nested call return at once instead of
        # running the teardown inside itself.
        self._close_lock = threading.RLock()
        self._closing = False

        # Structured outcome of the most recent turn (see the ``last_turn``
        # property). Populated in ``runtime/turn.py``'s finally; None until the
        # first turn completes.
        self._last_turn_outcome: Optional["TurnOutcome"] = None

        # Conversation history
        self.messages: List[Dict[str, Any]] = []

        # Plan session (shared with CLI; Agent reads via _plan_mode property)
        self._plan_session: PlanSession = plan_session or PlanSession()

        # When the host injects a ``project_instructions`` string,
        # skip the AGENTAO.md disk read and use the override verbatim.
        if project_instructions is not None:
            self.project_instructions = project_instructions
        else:
            self.project_instructions = self._load_project_instructions()

    def _init_replay(self, replay_config: Optional["ReplayConfig"]) -> None:
        """Attach a ReplayManager when a replay config was supplied.

        Replay state lives on a separate ``ReplayManager``. This is the one
        place a manager is built from a config: ``build_from_environment``
        passes its ``replay_config`` here rather than attaching one itself.
        With ``None`` (the default) ``replay_manager`` stays ``None`` until
        ``start_replay()`` / ``reload_replay_config()`` creates one.
        """
        self.replay_manager: Optional["ReplayManager"] = None
        if replay_config is not None:
            from .replay import ReplayManager as _ReplayManager
            self.replay_manager = _ReplayManager(self, config=replay_config)

    def _wire_tooling(
        self,
        *,
        mcp_manager: Optional["McpClientManager"],
        bg_store: Optional["BackgroundTaskStore"],
        sandbox_policy: Optional[SandboxPolicy],
        enable_builtin_agents: bool,
    ) -> None:
        """Build the tool registry, MCP tools, sub-agents, and tool runner.

        Ordering is load-bearing: ``bg_store`` / ``sandbox_policy`` must be
        set before ``_register_tools`` / ``_register_agent_tools`` (the
        sub-agent wrapper captures ``sandbox_policy`` at registration time),
        and ``_init_host_emitters`` must run before ``_register_agent_tools``
        so the wrapper captures a live ``HostSubagentEmitter`` rather than
        ``None``.
        """
        self.bg_store: Optional["BackgroundTaskStore"] = bg_store
        # Whether this runtime's chat loop consumes the store's notification
        # queue. A sub-agent shares its parent's store so the check / cancel
        # tools resolve, but the queue is drained, not read: whichever loop
        # reaches it first takes every notification. The wrapper turns this
        # off on the sub-agents it builds, so results reach the top-level
        # conversation only.
        self._drains_background_notifications = True
        # Must be set before _register_agent_tools(): the sub-agent wrapper
        # captures this via getattr(agent, "sandbox_policy", ...) at
        # registration time, so a late assignment leaves sub-agents
        # unsandboxed.
        self.sandbox_policy: Optional[SandboxPolicy] = sandbox_policy

        # Initialize tool registry
        self.tools = ToolRegistry()
        self._register_tools()

        # When an already-built manager is injected, skip the file
        # discovery pass entirely; the host owns the lifecycle. We still
        # have to wrap and register every tool the manager exposes, or
        # the model can't see any of them.
        if mcp_manager is not None:
            self.mcp_manager = mcp_manager
            register_mcp_tools(self, mcp_manager)
        else:
            self.mcp_manager = self._init_mcp()
            self._built_mcp_manager = self.mcp_manager

        # Host emitter setup MUST run before ``_register_agent_tools``
        # so the sub-agent wrapper captures a live ``HostSubagentEmitter``
        # instead of ``None`` — otherwise no subagent lifecycle events
        # ever reach the public stream for normal Agentao instances.
        self._init_host_emitters()

        # Initialize agent manager and register agent tools. Built-in
        # sub-agents are opt-in so the default tool schema stays compact;
        # project/plugin agents remain available when configured.
        self.agent_manager = AgentManager(
            project_root=self._working_directory,
            include_builtin_agents=enable_builtin_agents,
        )
        self._register_agent_tools()

        # Host ``extra_tools`` register last — after built-in, MCP, and
        # agent tools — so a same-named entry overrides built-in / agent
        # tools (the ``mcp_`` prefix is banned, so never MCP). See
        # ``register_extra_tools``.
        register_extra_tools(self)

        # Host ``enabled_tools`` allowlist — final prune pass, after all
        # registration so the typo guard validates against the live registry.
        # No-op when ``enabled_tools`` is None. See host-tool-allowlist.md.
        apply_enabled_tools(self)

        if self.bg_store is not None:
            self.bg_store.recover()

        # Initialize tool runner (encapsulates 4-phase tool execution pipeline)
        self.tool_runner = ToolRunner(
            tools=self.tools,
            permission_engine=self.permission_engine,
            transport=self.transport,
            logger=self.llm.logger,
            sandbox_policy=self.sandbox_policy,
            host_tool_emitter=self._host_tool_emitter,
            host_permission_emitter=self._host_permission_emitter,
            working_directory=self._working_directory,
        )
        # The MCP Skills gate reads the *session's* held entries through the
        # live skill manager, so a sub-agent — whose manager is a child view
        # sharing that object — is gated by the parent's loads and the
        # parent by its own (docs/design/mcp-skills.md §5.5 step 3, §6.2).
        # Compaction reads the session's MCP origins too (see
        # ``ContextManager.commit_compaction``).
        self.context_manager.mcp_origins_provider = self._mcp_session_origins
        self.tool_runner.set_mcp_skill_gate(
            self._mcp_skill_gate,
            begin_batch=self._mcp_skill_begin_batch,
            end_batch=self._mcp_skill_end_batch,
            on_confirmed=self._mcp_skill_confirmed,
            admit=self._mcp_admit,
        )

    def _mcp_skill_gate(self, tool_name: str, args: Dict[str, Any], tool: Any) -> Optional[str]:
        mcp_skills = getattr(self.skill_manager, "mcp_skills", None)
        if mcp_skills is None:
            orphan = getattr(self.skill_manager, "mcp_orphan_origins", None)
            if isinstance(orphan, set) and orphan:
                from .mcp.skills import restored_gate_note

                return restored_gate_note(tool_name, tool, sorted(orphan))
            return None
        if tool_name == "activate_skill" and isinstance(args, dict):
            # A disabled skill is refused at activation whatever the user
            # answers, so asking consent for it would be a prompt for nothing.
            # Not a bypass: activation still requires the approved manifest.
            disabled = getattr(self.skill_manager, "disabled_skills", None) or ()
            if args.get("skill_name") in disabled:
                return None
        return mcp_skills.gate(
            tool_name, args, tool, view=getattr(self.skill_manager, "mcp_view", None),
        )

    def _mcp_skill_begin_batch(self, arguments: Any) -> Any:
        mcp_skills = getattr(self.skill_manager, "mcp_skills", None)
        if mcp_skills is None:
            return None
        return mcp_skills.begin_batch(
            arguments, view=getattr(self.skill_manager, "mcp_view", None),
        )

    def _mcp_session_origins(self) -> set:
        """Every MCP origin this conversation carries: loaded, pending, restored."""
        manager = getattr(self, "skill_manager", None)
        origins: set = set()
        orphan = getattr(manager, "mcp_orphan_origins", None)
        if isinstance(orphan, set):
            origins |= orphan
        mcp_skills = getattr(manager, "mcp_skills", None)
        if mcp_skills is not None:
            origins |= set(mcp_skills.origins(getattr(manager, "mcp_view", None)))
        return origins

    def _mcp_admit(self, text: Any) -> Any:
        """Let text carrying an MCP-origin marker into this conversation.

        A sub-agent result or a background notification can carry another
        conversation's MCP skill content — after ``/clear``, or from a task
        started before it. Its marker's origins count as loaded here, so the
        §6 gates come back on before the next call is planned. With no
        Skills session to gate with, the text is withheld instead.
        """
        from .skills.provenance import summary_origins

        labels = summary_origins(text)
        if not labels:
            return text
        mcp_skills = getattr(self.skill_manager, "mcp_skills", None)
        if mcp_skills is None:
            # Origins the restore-only gate already covers are gated here
            # as they are in the transcript: a sub-agent of a restored
            # session inherits them and marks every result with them, and
            # withholding those would withhold every sub-agent's answer.
            orphan = getattr(self.skill_manager, "mcp_orphan_origins", None)
            if isinstance(orphan, set) and labels <= orphan:
                return text
            from .embedding.sessions import withheld

            return withheld(labels)
        mcp_skills.taint(labels, getattr(self.skill_manager, "mcp_view", None))
        return text

    def _mcp_skill_confirmed(self, tool_name: str, args: Dict[str, Any], note: str) -> None:
        mcp_skills = getattr(self.skill_manager, "mcp_skills", None)
        if mcp_skills is not None:
            mcp_skills.confirmed(
                tool_name, args, view=getattr(self.skill_manager, "mcp_view", None), note=note,
            )

    def _mcp_skill_end_batch(self, token: Any) -> None:
        mcp_skills = getattr(self.skill_manager, "mcp_skills", None)
        if mcp_skills is not None:
            mcp_skills.end_batch(token)

    def _init_host_emitters(self) -> None:
        """Build the tool / permission / subagent host-event emitters.

        Must run before ``_register_agent_tools`` so the sub-agent wrapper
        captures a live ``HostSubagentEmitter`` rather than ``None``.
        """
        from .host.projection import (
            HostPermissionEmitter,
            HostSubagentEmitter,
            HostToolEmitter,
        )
        self._host_tool_emitter = HostToolEmitter(
            self._host_events,
            session_id_provider=lambda: self._session_id,
            turn_id_provider=lambda: self._current_turn_id,
        )
        self._host_permission_emitter = HostPermissionEmitter(
            self._host_events,
            session_id_provider=lambda: self._session_id,
            turn_id_provider=lambda: self._current_turn_id,
            active_permissions_provider=self.active_permissions,
        )
        self._host_subagent_emitter = HostSubagentEmitter(
            self._host_events,
            parent_session_id_provider=lambda: self._session_id,
        )

    def _init_skill_and_memory(
        self,
        skill_manager: Optional[SkillManager],
        memory_manager: Optional["MemoryManager"],
    ) -> None:
        """Wire the skill manager, memory store, and memory tooling.

        Both subsystems honour host injection: a pre-loaded
        ``SkillManager`` skips the disk auto-discovery scan, and an
        injected ``MemoryManager`` (CLI/ACP factory) carries both project
        and user stores — bare construction falls back to project scope.
        """
        # When the host has constructed and pre-loaded its own
        # ``SkillManager``, skip the auto-discovery scan entirely.
        if skill_manager is not None:
            self.skill_manager = skill_manager
        else:
            self.skill_manager = SkillManager(
                working_directory=self._working_directory,
            )
        from .memory import MemoryManager, MemoryRetriever, SQLiteMemoryStore
        from .memory.render import MemoryPromptRenderer
        if memory_manager is not None:
            self._memory_manager = memory_manager
        else:
            # Pure-injection / bare-construction path: project scope only.
            # The CLI / ACP factory passes an explicitly-built MemoryManager
            # with both project and user stores resolved from the
            # surrounding environment, so cross-project user memory only
            # surfaces through that path.
            self._memory_manager = MemoryManager(
                project_store=SQLiteMemoryStore.open_or_memory(
                    self.working_directory / ".agentao" / "memory.db"
                ),
            )
            self._built_memory_manager = self._memory_manager
        self.memory_tool = SaveMemoryTool(memory_manager=self._memory_manager)
        self.memory_retriever = MemoryRetriever(self._memory_manager)
        self.memory_renderer = MemoryPromptRenderer()

    def _resolve_llm_client(
        self,
        llm_client: Optional[LLMClient],
        *,
        api_key: Optional[str],
        base_url: Optional[str],
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        extra_body: Optional[Dict[str, Any]],
        prompt_cache: Optional[str],
        prompt_cache_ttl: Optional[str],
        api_format: Optional[str],
        logger: Optional[logging.Logger],
    ) -> LLMClient:
        """Return the injected client, or build one from raw provider config.

        The log file is anchored to ``working_directory`` so it always
        resolves to an absolute, writable path (ACP launches run from a
        read-only ``/``).
        """
        if llm_client is not None:
            return llm_client
        if not api_key or not base_url or not model:
            raise ValueError(
                "Agentao(): api_key, base_url, and model are required "
                "when llm_client is not supplied. Pass them explicitly, "
                "inject a pre-built llm_client=, or use "
                "agentao.embedding.build_from_environment() for "
                "CLI-style env auto-discovery."
            )
        llm_kwargs: Dict[str, Any] = dict(
            api_key=api_key,
            base_url=base_url,
            model=model,
            log_file=str(self.working_directory / "agentao.log"),
            logger=logger,
        )
        if temperature is not None:
            llm_kwargs["temperature"] = temperature
        if max_tokens is not None:
            llm_kwargs["max_tokens"] = max_tokens
        if extra_body is not None:
            llm_kwargs["extra_body"] = extra_body
        if prompt_cache is not None:
            llm_kwargs["prompt_cache"] = prompt_cache
        if prompt_cache_ttl is not None:
            llm_kwargs["prompt_cache_ttl"] = prompt_cache_ttl
        if api_format is not None:
            llm_kwargs["api_format"] = api_format
        return LLMClient(**llm_kwargs)

    @property
    def _llm_config(self) -> Dict[str, Any]:
        """Live snapshot of the parent's effective provider config.

        Read at every access so sub-agents launched after a runtime
        ``set_model`` / ``maxTokens`` change inherit the active values
        rather than the construction-time snapshot.
        """
        return {
            "api_key": self.llm.api_key,
            "base_url": self.llm.base_url,
            "model": self.llm.model,
            "temperature": self.llm.temperature,
            "omit_temperature": getattr(self.llm, "omit_temperature", False),
            "max_tokens": self.llm.max_tokens,
            # Sub-agents inherit the parent's request-body passthrough
            # (reasoning_effort / provider-mandatory fields) the same way they
            # inherit temperature/max_tokens; ``None`` when unset so the
            # sub-agent's raw-config build simply omits it. ``or None`` maps an
            # empty dict to "unset".
            "extra_body": getattr(self.llm, "extra_body", None) or None,
            # Inherited for the same reason as extra_body: a sub-agent talks to
            # the *same endpoint*, so whether that endpoint honours explicit
            # cache breakpoints is a property of the deployment, not of who is
            # asking. ``None`` when unset so the raw-config build omits it.
            "prompt_cache": getattr(self.llm, "prompt_cache", None),
            "prompt_cache_ttl": getattr(self.llm, "prompt_cache_ttl", None),
            # The wire protocol is a property of the endpoint too: a sub-agent
            # that fell back to the default would speak Chat Completions at a
            # Messages endpoint. ``None`` for an injected client that has no
            # such attribute, which leaves the default in place.
            "api_format": getattr(self.llm, "api_format", None),
            # Not provider config, but read from the same place and for the
            # same reason: a sub-agent built without it constructs an
            # ``LLMClient`` with ``logger=None``, and that path *evicts and
            # closes* the ``agentao.log`` handler already on the package
            # logger before installing its own. From a background sub-agent's
            # thread that closes a file the parent is still logging to.
            # Handing the logger over skips the package-root mutation entirely
            # and lands the child's LLM traffic in the parent's log.
            "logger": self.llm.logger,
        }

    def add_host_event_observer(self, callback: _ObserverT) -> _ObserverT:
        """Register a synchronous observer on the public host event stream.

        Pass-through to :meth:`EventStream.add_observer` for sync
        consumers that cannot drive the async ``events()`` iterator.
        The callback fires inline on the producer thread; it must be
        cheap and non-blocking. Raised exceptions are logged and
        discarded by :class:`EventStream`. Returns ``callback`` itself.
        """
        self._host_events.add_observer(callback)
        return callback

    def remove_host_event_observer(self, callback: Callable[["HostEvent"], object]) -> bool:
        """Detach a previously registered observer. Idempotent."""
        return self._host_events.remove_observer(callback)

    def add_event_observer(self, callback: _ObserverT) -> _ObserverT:
        """Backward-compatible alias for :meth:`add_host_event_observer`."""
        return self.add_host_event_observer(callback)

    def remove_event_observer(self, callback: Callable[["HostEvent"], object]) -> bool:
        """Backward-compatible alias for :meth:`remove_host_event_observer`."""
        return self.remove_host_event_observer(callback)

    def events(self, session_id: Optional[str] = None) -> AsyncGenerator["HostEvent", None]:
        """Return an async iterator over public host events.

        Delivery semantics (see ``docs/reference/host-api.md`` for the full
        contract):

        - No replay: events emitted before the first subscription are
          discarded.
        - Same-session ordering is guaranteed.
        - Bounded backpressure: a slow consumer blocks the producer for
          matching events rather than dropping them.
        - Cancellation of the iterator releases queue resources.

        ``session_id=None`` subscribes to every session owned by this
        ``Agentao`` instance; passing a string narrows the filter.
        """
        return self._host_events.subscribe(session_id=session_id)

    def active_permissions(self) -> "ActivePermissions":
        """Return a host-facing :class:`ActivePermissions` snapshot.

        The runtime delegates to :meth:`PermissionEngine.active_permissions`
        when an engine is configured. When the host has not injected an
        engine, the runtime falls back to per-tool ``requires_confirmation``
        — write-capable tools are NOT categorically blocked, only those
        that flag themselves prompt for confirmation. Reporting
        ``mode="read-only"`` would be stricter than the runtime
        actually enforces and would mislead status displays and public
        permission-decision events. ``workspace-write`` is the closest
        public mode to the engine-less behaviour; the
        ``no-engine`` source label tells hosts they are seeing the
        permissive fallback rather than a configured policy.
        """
        from .host.models import ActivePermissions
        if self.permission_engine is not None:
            return self.permission_engine.active_permissions()
        return ActivePermissions(
            mode="workspace-write",
            rules=[],
            loaded_sources=["default:no-engine"],
        )

    def add_tool(self, tool: "RegistrableTool", *, replace: bool = False) -> None:
        """Register a tool after construction (runtime dual of ``extra_tools=``).

        Visible to the model on the **next** ``chat()`` / ``arun()`` call — the
        *schema* is snapshotted once per call before the LLM loop, so a mid-turn
        add never changes what the model sees in-flight. Tool *execution*
        resolves names against the live registry, so v1 supports calling this
        between turns only — not from a concurrent task or a tool's ``execute()``
        (see ``docs/design/runtime-tool-injection.md`` §7).

        Routes through the same validation + capability binding as
        ``extra_tools=``:

        * rejects reserved names — ``mcp_`` prefix and ``_PLAN_ONLY_TOOLS``
          (see :meth:`_reject_reserved_tool_name`) — and empty / non-string names;
        * binds ``working_directory`` / ``filesystem`` / ``shell`` so the tool
          is never "bare" (ACP cwd isolation, host FS/shell redirection);
        * ``replace=False`` + a name clash raises (stricter than ``register``'s
          warn-and-overwrite — an explicit host call should be explicit); pass
          ``replace=True`` to override a built-in / agent / extra tool, which is
          silent save for an INFO audit line.
        """
        from .tooling.registry import _bind_and_register

        self._validate_one_extra_tool(tool, context="add_tool()")
        already = tool.name in self.tools.tools
        if already and not replace:
            raise ValueError(
                f"add_tool(): a tool named '{tool.name}' is already "
                "registered. Pass replace=True to override it intentionally."
            )
        if already:
            _logger.info(
                "add_tool: '%s' (%s) overrides an already-registered tool",
                tool.name,
                type(tool).__name__,
            )
        _bind_and_register(self, tool, replace=already, origin="host")

    def remove_tool(self, name: str) -> bool:
        """Unregister a tool after construction. Returns whether it existed.

        Invisible to the model on the **next** ``chat()`` / ``arun()`` call
        (same schema-snapshot semantics as :meth:`add_tool`; execution is live,
        so call between turns only). An unknown name returns ``False`` rather
        than raising.

        Reserved names raise: ``mcp_`` tools belong to the MCP lifecycle and
        ``_PLAN_ONLY_TOOLS`` to the plan-mode state machine — neither is removed
        here. Built-in / extra / agent tools can be removed. This shrinks the
        schema the model sees; it is **not** a security boundary (that stays
        with the permission engine).
        """
        self._reject_reserved_tool_name(name, context="remove_tool()")
        return self.tools.unregister(name)

    @property
    def working_directory(self) -> Path:
        """Effective working directory for this runtime.

        Frozen at construction (required keyword arg since 0.3.0).
        Two Agentao instances created with different
        ``working_directory`` values report independent paths even in
        the same process. ``os.chdir`` inside the host has no effect on
        an already-constructed Agentao.
        """
        return self._working_directory

    @property
    def memory_manager(self) -> "MemoryManager":
        return self._memory_manager

    @memory_manager.setter
    def memory_manager(self, manager: "MemoryManager") -> None:
        """Replace the memory manager and keep all dependent helpers in sync."""
        self._memory_manager = manager
        self.memory_tool.memory_manager = manager
        self.memory_retriever._manager = manager
        self.context_manager.memory_manager = manager

    def _load_project_instructions(self) -> Optional[str]:
        # Implementation lives in :mod:`agentao.prompts.helpers`. Kept as a
        # thin facade so tests and external callers that patch the agent
        # method keep working.
        return load_project_instructions(self.working_directory, self.llm.logger)

    def _register_tools(self):
        # Implementation lives in ``agentao.tooling.registry`` — see that
        # module for the tool list and working-directory binding logic.
        register_builtin_tools(self)

    def _init_mcp(self) -> Optional["McpClientManager"]:
        # Implementation lives in ``agentao.tooling.mcp_tools`` — see that
        # module for config merge semantics and error handling.
        return init_mcp(self)

    def close(self) -> None:
        """Clean up resources (MCP connections, event loops).

        NOTE: SessionEnd hooks are dispatched by the CLI layer
        (on_session_end / _dispatch_session_end_hooks), which runs them
        before close() on every CLI exit path.  close() does NOT dispatch
        them, to avoid double-firing there, so neither does ``with`` /
        ``async with`` / :meth:`aclose` for an embedded host.

        Releases what the agent built itself: the MCP manager (when no
        ``mcp_manager=`` was passed), the memory manager (when no
        ``memory_manager=`` was passed) and ``<working_directory>/agentao.log``
        (when it built its own LLM client and no ``logger=`` was passed). An
        injected ``mcp_manager=``, ``memory_manager=`` or ``llm_client=`` is
        the caller's to release, and so is a manager the caller assigns to
        ``mcp_manager`` / ``memory_manager`` afterwards; the one the agent
        built is still released.

        Safe to call more than once, and from more than one thread: calls
        are serialized. A later call skips the MCP disconnect and repeats the
        replay end, the memory-store close and the log close, all no-ops then.
        A call re-entered on the closing thread (a signal handler during the
        teardown) returns at once.
        """
        with self._close_lock:
            if self._closing:
                return  # re-entered on this thread from inside the teardown
            try:
                # Set inside the ``try``: a KeyboardInterrupt landing between
                # the set and the ``try`` would leave the flag stuck, and every
                # later ``close()`` would return without closing.
                self._closing = True
                if self.replay_manager is not None:
                    try:
                        self.replay_manager.end()
                    except Exception:
                        pass
                # Only what this agent built: an ``mcp_manager=`` /
                # ``memory_manager=`` the host passed in is the host's to
                # release, and may be shared with other agents.
                mcp_manager = self._built_mcp_manager
                if mcp_manager is not None:
                    try:
                        mcp_manager.disconnect_all()
                    except Exception as e:
                        self.llm.logger.warning(f"Error disconnecting MCP: {e}")
                    # Only once the disconnect has returned: a Ctrl-C inside it
                    # leaves the manager owned, so a later ``close()`` runs the
                    # disconnect again, which waits for the first one to finish.
                    self._built_mcp_manager = None
                    if self.mcp_manager is mcp_manager:
                        self.mcp_manager = None
                # The memory stores hold no connection between calls on a file backing,
                # but a transient ``:memory:`` store does, and a host that is done with an
                # agent should not have to wait for the collector to get it back.
                memory_manager = self._built_memory_manager
                if memory_manager is not None:
                    try:
                        memory_manager.close()
                    except Exception as e:
                        self.llm.logger.warning(f"Error closing memory stores: {e}")
                # Last, so the warnings above still reach ``agentao.log``. Its
                # open handle is what kept a host from deleting the working
                # directory on Windows (WinError 32).
                self._close_built_llm_client()
            finally:
                self._closing = False

    async def aclose(self) -> None:
        """Async :meth:`close`: runs it on a worker thread so the loop keeps going.

        Like ``await asyncio.to_thread(agent.close)``, but on a thread of its
        own: a cancelled ``to_thread`` whose work is still queued behind a
        busy default executor never runs it, which would leave the agent
        open, and the default executor stays free for the loop's own work
        (``getaddrinfo`` for httpx). End the agent's turns first: neither this
        nor :meth:`close` waits for or cancels one in progress. ``async with``
        around ``await agent.arun()`` meets that when the task is cancelled
        too, because a cancelled ``arun()`` cancels its turn and waits up to
        5 seconds for it before re-raising; a turn still running after that
        (a blocking tool that ignores the token), or when a second cancel
        cuts that wait short, is closed under.

        Cancelling the ``await`` stops the waiting, not ``close()``: it
        finishes on its worker thread, and a warning says so.
        """
        work: "concurrent.futures.Future[None]" = concurrent.futures.Future()
        # Marked running before the thread exists, so cancelling the awaiter
        # cannot cancel the work (``wrap_future`` propagates ``cancel()``).
        work.set_running_or_notify_cancel()
        ctx = contextvars.copy_context()

        def _run() -> None:
            try:
                ctx.run(self.close)
            except BaseException as e:  # handed to the awaiter, if any
                work.set_exception(e)
            else:
                work.set_result(None)

        # Not a daemon, explicitly: a new thread inherits the creating
        # thread's flag, and a host running its loop on a daemon thread
        # would otherwise let interpreter exit cut the teardown short.
        try:
            threading.Thread(
                target=_run, name="agentao-aclose", daemon=False
            ).start()
        except RuntimeError:
            # No new thread (interpreter shutdown has begun, or the process
            # is at its thread limit): closing on the loop thread blocks it,
            # but beats leaving the agent open.
            self.close()
            return
        try:
            await asyncio.wrap_future(work)
        except asyncio.CancelledError:
            if not work.done():
                _logger.warning(
                    "aclose cancelled; close() is still running on a worker thread"
                )
            # Nobody will read the result now, so a failure is logged here,
            # including one that landed in the window before the cancel (a
            # done callback runs at once on a finished future).
            work.add_done_callback(_log_abandoned_close_failure)
            raise

    def __enter__(self: _AgentT) -> _AgentT:
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional["TracebackType"],
    ) -> None:
        self.close()

    async def __aenter__(self: _AgentT) -> _AgentT:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional["TracebackType"],
    ) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Session Replay lifecycle — supported delegation surface.
    # ``start_replay`` / ``end_replay`` / ``reload_replay_config`` (below)
    # are LIVE, actively-called API: the CLI (``cli/session.py``,
    # ``cli/commands/sessions.py``, ``cli/replay_commands.py``) and the ACP
    # server (``acp/session_new.py`` / ``acp/session_load.py``) all call them.
    # They are thin delegations onto :class:`agentao.replay.ReplayManager`;
    # embedded hosts may instead call ``agent.replay_manager.start()`` /
    # ``end()`` / ``reload_config()`` directly. The recorder, adapter, host
    # sink and config are read off ``agent.replay_manager`` (``None`` until
    # something attaches one); the four private ``_replay_*`` views that
    # used to mirror them here were removed in 0.5.0.
    # ------------------------------------------------------------------

    def _ensure_replay_manager(self) -> "ReplayManager":
        if self.replay_manager is None:
            from .replay import ReplayManager
            self.replay_manager = ReplayManager(self)
        return self.replay_manager

    def start_replay(self, session_id: Optional[str] = None) -> Optional[Path]:
        return self._ensure_replay_manager().start(session_id)

    def end_replay(self) -> None:
        if self.replay_manager is not None:
            self.replay_manager.end()

    def reload_replay_config(self) -> "ReplayConfig":
        return self._ensure_replay_manager().reload_config()

    @property
    def last_turn(self) -> "Optional[TurnOutcome]":
        """Structured outcome of the most recent ``chat()`` / ``arun()`` turn.

        ``None`` before the first turn completes. Those methods return the
        turn's text as a ``str``; this is the companion that says whether that
        text is a real answer. Read ``agent.last_turn.is_answer`` (or branch on
        ``agent.last_turn.incomplete_reason``) before treating the reply as the
        model's — the harness substitutes a placeholder / canned notice for an
        answerless, halted, or failed turn, and the bare string cannot be told
        apart from a real one. Mirrors the ``TURN_END`` transport payload, so a
        host gets the same facts without subscribing to the internal channel.
        """
        return self._last_turn_outcome

    def _register_agent_tools(self):
        # Implementation lives in ``agentao.tooling.agent_tools`` — see
        # that module for event wiring and callback bridging.
        register_agent_tools(self)

    def _build_system_prompt(self) -> str:
        """Build the system prompt for one turn — the stable prefix only.

        Composition lives in :class:`agentao.prompts.SystemPromptBuilder`;
        this method stays as a thin entry point so existing callers and
        tests keep working unchanged.

        Active-skill bodies, todos, dynamic recall and the plan prompt are
        **not** in the returned string; they ride the request-only tail built
        by :meth:`_build_volatile_tail`. The available-skills *catalogue* is
        here — it lists active skills too, so an activation does not change it.
        """
        return SystemPromptBuilder(self).build()

    def _build_volatile_tail(self) -> str:
        """Build the request-only volatile tail, or ``""`` when empty.

        One ``<system-reminder>`` block carrying active-skill bodies, todos,
        dynamic recall and the plan prompt. The chat loop appends it to the
        *outgoing request* as a trailing ``user`` message and never to
        ``self.messages`` — see
        :meth:`agentao.prompts.SystemPromptBuilder.build_volatile_tail`.
        """
        return SystemPromptBuilder(self).build_volatile_tail()

    def _extract_context_hints(self) -> List[str]:
        # Implementation lives in :mod:`agentao.prompts.helpers`. Kept as a
        # thin facade so ``SystemPromptBuilder`` and tests that call this
        # as an agent method keep working.
        return extract_context_hints(self.messages)

    @property
    def compaction_coordinator(self) -> "CompactionCoordinator":
        """The single orchestrator every compaction entry point goes through.

        Lazily built and then held: the coordinator is where compaction state
        that outlives one call belongs, so a fresh instance per call would
        quietly drop it.

        Imported here rather than at module scope so that
        ``agentao/compaction/__init__.py`` stays free of the coordinator: that
        ``__init__`` runs on every import of ``agentao.compaction.types``,
        including the one in ``agentao/plugins/hooks/_payload.py``, and
        dragging ``coordinator -> context_manager ->`` the LLM stack through
        it would put the whole LLM stack behind a stdlib-only vocabulary
        import.
        """
        if self._compaction_coordinator is None:
            from .compaction.coordinator import CompactionCoordinator
            self._compaction_coordinator = CompactionCoordinator(self)
        return self._compaction_coordinator

    def compact(self, *, reason: "ManualCompactionReason" = "manual_cli") -> "CompactionOutcome":
        """Compact conversation history now, and say what happened.

        The public compaction entry. Before it existed every caller reached
        straight into ``context_manager``, which is how five entry points came
        to disagree about what a compaction had done — a bare list cannot say
        whether it changed, why it did not, or whether the failure counted.

        ``reason`` picks which entry point this is on behalf of and therefore
        which policy applies: ``manual_cli`` (the default) and
        ``api_overflow`` are allowed through an open circuit breaker as
        half-open probes, ``compression_threshold`` is not.

        Returns the :class:`~agentao.compaction.types.CompactionOutcome`;
        ``outcome.status`` is ``success | cancelled | failed | skipped`` and
        ``outcome.detail`` says which case. History is left byte-identical on
        every status but ``success``.
        """
        from .compaction.coordinator import CompactionRequest
        run = self.compaction_coordinator.run(
            CompactionRequest(
                "manual" if reason == "manual_cli" else "auto", "full", reason,
            ),
            system_prompt=self._build_system_prompt(),
            measure_system_tokens=True,
        )
        return run.outcome

    def _llm_call(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
                  cancellation_token: Optional[CancellationToken] = None) -> Any:
        # Implementation lives in :mod:`agentao.runtime.llm_call`. Kept as
        # a thin facade because ``ChatLoopRunner`` calls this as
        # ``agent._llm_call(...)`` and external tests patch it by name.
        return run_llm_call(self, messages, tools, cancellation_token)

    def add_message(self, role: str, content: Union[str, List[Dict[str, Any]]]) -> None:
        """Add a message to conversation history.

        Args:
            role: Message role (user/assistant/system)
            content: Message content — a plain string, or an OpenAI-style
                multimodal content list (e.g. ``text`` + ``image_url`` parts).
        """
        self.messages.append({"role": role, "content": content})

    def clear_history(self) -> None:
        """Clear conversation history, deactivate all skills, and reset todos.

        Background sub-agents keep running, but stop reporting here: their
        completion notifications are addressed to the conversation that
        launched them, and the chat loop drains the queue into whatever
        history exists at the next turn. See
        ``BackgroundTaskStore.start_new_conversation``.
        """
        self.messages = []
        if self.bg_store is not None:
            self.bg_store.start_new_conversation()
        self.skill_manager.clear_active_skills()
        # The acting window of every loaded MCP skill ends with the
        # conversation that held its SKILL.md, and the §6 gates lift with it.
        mcp_skills = getattr(self.skill_manager, "mcp_skills", None)
        # A sub-agent's manager is a view of the parent's session: only the
        # session's own reader starts a new generation (as on restore).
        if mcp_skills is not None and getattr(self.skill_manager, "mcp_view", None) is None:
            mcp_skills.clear_held()
        if isinstance(getattr(self.skill_manager, "mcp_orphan_origins", None), set):
            self.skill_manager.mcp_orphan_origins = set()
        self.todo_tool.clear()
        # Reset context and session token counters for the fresh session
        self.context_manager.invalidate_token_anchor()
        # The compaction breaker counts *this* conversation's failures, so
        # replacing the conversation invalidates its evidence. Without this a
        # session that tripped it stayed unable to auto-compact across
        # ``/clear``, because the only other reset is a successful compaction
        # and the open breaker is what prevents one on the automatic path.
        self.context_manager.reset_compaction_circuit()
        # Under the client's lock when it has one: a background sub-agent may
        # be adding its usage from another thread. A host-injected client
        # without ``reset_usage`` keeps the two plain writes it always had.
        reset_usage = getattr(self.llm, "reset_usage", None)
        if callable(reset_usage):
            reset_usage()
        else:
            self.llm.total_prompt_tokens = 0
            self.llm.total_completion_tokens = 0

    def _cached_usage_note(self) -> str:
        """`` (N cached, M cache-write)`` for the session line, or ``""``.

        Both are parts of the prompt total, shown because a provider bills
        them at a different rate. Type-checked: ``self.llm`` may be a host's
        own object, or a mock that answers any attribute.
        """
        from .llm._usage import positive_int

        read = positive_int(getattr(self.llm, "total_cache_read_tokens", None))
        creation = positive_int(getattr(self.llm, "total_cache_creation_tokens", None))
        parts = ([f"{read:,} cached"] if read else []) + (
            [f"{creation:,} cache-write"] if creation else []
        )
        return f" ({', '.join(parts)})" if parts else ""

    @property
    def _plan_mode(self) -> bool:
        """Whether plan mode is active (reads from shared PlanSession)."""
        return self._plan_session.is_active

    def chat(self, user_message: str, max_iterations: int = 100,
             cancellation_token: Optional[CancellationToken] = None,
             images: Optional[List[Dict[str, str]]] = None) -> str:
        """Process user message and generate response.

        Args:
            user_message: User's message
            max_iterations: Maximum number of tool call iterations to prevent infinite loops
            cancellation_token: Optional token to cancel this chat() call. If not provided,
                                 a fresh token is created. Pass a shared token to propagate
                                 cancellation from a parent agent (Gemini CLI pattern).
            images: Optional list of image attachments, each a dict with
                    ``data`` (base64-encoded string) and ``mimeType`` (e.g.
                    ``image/png``). Surfaced as OpenAI ``image_url`` parts.

        Returns:
            Assistant's response
        """
        # Per-turn lifecycle (cancellation + counters + replay begin/end_turn +
        # KeyboardInterrupt/AgentCancelledError mapping) lives in
        # :mod:`agentao.runtime.turn`; this method stays as a thin facade
        # so external callers and tests keep using ``Agentao.chat``.
        return run_turn(self, user_message, max_iterations, cancellation_token,
                        images=images)

    async def arun(
        self,
        user_message: str,
        max_iterations: int = 100,
        cancellation_token: Optional[CancellationToken] = None,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Async wrapper around :meth:`chat` for embedded async hosts.

        Runtime internals stay sync (the chat loop, tool execution,
        permission, and replay surfaces are all sequential I/O). This
        method bridges through ``run_in_executor`` so async hosts can
        ``await agent.arun(...)`` without their own thread bridge while
        the same turn lifecycle from :meth:`chat` runs unchanged. The
        executor is agentao's own, never the loop's default — see
        :func:`_get_arun_pool` for the deadlock that choice avoids.

        Cancellation, replay, and ``max_iterations`` behave identically
        across both surfaces; the executor thread reads the same
        cancellation token. If the awaiting task is cancelled (e.g.
        ``asyncio.wait_for`` timeout, client disconnect) we forward the
        signal to the in-flight ``chat()`` call so the executor thread
        actually winds down instead of running to completion against
        the now-detached host.
        """
        loop = asyncio.get_running_loop()
        token = cancellation_token if cancellation_token is not None else CancellationToken()
        # Capture the host loop on the token so the AsyncTool dispatcher
        # in ToolExecutor can bridge coroutines back onto the loop that
        # owns any host-affine resources (aiohttp sessions, async DB
        # pools, anyio task groups). Sync chat() callers leave the field
        # ``None`` and the dispatcher falls back to ``asyncio.run``,
        # which only supports loop-independent async tools.
        token.runtime_loop = loop
        # Forward ``images`` only when present so the executor keeps calling
        # ``chat`` with the historical three positional args for text turns —
        # test/host stubs that patch ``chat`` with a 3-arg signature stay
        # working, while async hosts can still send multimodal input.
        pool = _get_arun_pool()
        # Submitted directly, not through ``run_in_executor``, to hold the
        # ``concurrent.futures.Future``: a turn still queued behind a busy
        # pool has to be cancellable outright (see the handler below).
        if images:
            work = pool.submit(self.chat, user_message, max_iterations, token, images)
        else:
            work = pool.submit(self.chat, user_message, max_iterations, token)
        future = asyncio.wrap_future(work, loop=loop)
        # Shielded so a cancel of this task does not cancel ``future`` with it:
        # a turn that has started is still unwinding (backfilling orphaned tool
        # results, emitting TURN_END), and waiting on it below needs it alive.
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            token.cancel(ASYNC_CANCEL_REASON)
            # A turn that never started is dropped here; left queued, it would
            # run later and write a user message nobody is waiting for.
            if not work.cancel():
                await _await_turn_cleanup(future, work)
            raise

    def astream(
        self,
        user_message: str,
        *,
        max_iterations: int = 100,
        images: Optional[List[Dict[str, str]]] = None,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> "AsyncGenerator[Union[TextDelta, TurnOutcome], None]":
        """Run one turn, yielding its text as it streams, then its outcome.

        Yields :class:`~agentao.host.TextDelta` items while the turn runs,
        then the turn's :class:`~agentao.host.TurnOutcome` as the last item
        when the turn returned. Deltas are for display; the answer is the
        outcome's ``text``, checked with ``is_answer``. Joined deltas are not
        the answer: narration before a tool call streams too, and a
        placeholder or error text is never streamed.

        Close the stream when leaving it early::

            async with contextlib.aclosing(agent.astream(prompt)) as stream:
                async for item in stream:
                    ...

        ``break`` alone does not close an async generator: it is closed when
        it is garbage-collected or the event loop shuts down, so while
        anything still references it, the turn stays open: once the queue
        is full it waits, still holding the agent, and a later turn raises
        ``TurnInProgressError``. Closing
        (``aclose()``, or cancelling the consuming task) cancels the turn and
        waits for its cleanup, bounded, like a cancelled :meth:`arun`.

        Same turn rules as :meth:`arun`: a second turn on the same agent
        raises ``TurnInProgressError``, from the iterator, when the worker
        starts it. An exception from the turn is raised from the iterator
        after the text already streamed. Tool and permission events stay on
        :meth:`events`. A sub-agent's text is not included.

        Raises ``TypeError`` here, before anything runs, when the agent's
        transport has no ``subscribe()``. The stream attaches by subscribing
        and never replaces the transport.
        """
        from .runtime.astream import resolve_subscribe, stream_turn

        subscribe = resolve_subscribe(self.transport)
        return stream_turn(
            self,
            user_message,
            subscribe,
            max_iterations=max_iterations,
            images=images,
            cancellation_token=cancellation_token,
        )

    def _chat_inner(self, user_message: str, max_iterations: int,
                    token: CancellationToken,
                    images: Optional[List[Dict[str, str]]] = None) -> str:
        """Inner chat loop — called by chat(). Raises AgentCancelledError on cancellation.

        Body lives in :class:`agentao.runtime.chat_loop.ChatLoopRunner`; this
        method stays as the entry point so subclasses or test patches
        targeting ``_chat_inner`` keep working.
        """
        return ChatLoopRunner(self).run(user_message, max_iterations, token,
                                        images=images)

    def get_conversation_summary(self) -> str:
        """Get a summary of the conversation.

        Returns:
            Conversation summary
        """
        tools_schema = self.tools.to_openai_format(plan_mode=self._plan_mode)
        # Headline count: self.messages only so a fresh session shows 0.
        # When Tier 1 API count is present it already reflects all overhead.
        stats = self.context_manager.get_usage_stats(self.messages)
        # Breakdown: include system prompt + tools only when there are messages,
        # so that /new resets all three components to 0.
        if self.messages:
            messages_with_system = [
                {"role": "system", "content": self._build_system_prompt()}
            ] + self.messages
            # The volatile tail is part of what the next request costs, so it
            # gets its own bucket. Leaving it out would report the
            # skills/todos/recall tokens as having vanished when 0a moved them
            # out of the system prompt — they did not, they moved messages.
            tail_text = self._build_volatile_tail()
            bd_full = self.context_manager.estimate_tokens_breakdown(
                messages_with_system, tools=tools_schema,
                tail=({"role": "user", "content": tail_text} if tail_text else None),
            )
        else:
            bd_full = {
                "system": 0, "messages": 0, "tail": 0, "tools": 0, "total": 0,
            }
        stats["token_breakdown"] = bd_full
        memory_count = len(self.memory_manager.get_all_entries())

        if not self.messages:
            summary = "No conversation history\n"
        else:
            summary = f"Messages: {len(self.messages)}\n"

        summary += f"Model: {self.llm.model}\n"
        summary += (
            "Temperature: provider default (not sent)\n" if self.llm.temperature is None
            else "Temperature: off (omitted)\n" if getattr(self.llm, "omit_temperature", False)
            else f"Temperature: {self.llm.temperature}\n"
        )
        summary += f"Active skills: {len(self.skill_manager.get_active_skills())}\n"
        summary += f"Saved memories: {memory_count}\n"
        todos = self.todo_tool.get_todos()
        if todos:
            done = sum(1 for t in todos if t["status"] == "completed")
            summary += f"Task list: {done}/{len(todos)} completed\n"

        # MCP server info
        if self.mcp_manager:
            statuses = self.mcp_manager.get_server_status()
            connected = sum(1 for s in statuses if s["status"] == "connected")
            total_tools = sum(s["tools"] for s in statuses)
            summary += f"MCP servers: {connected}/{len(statuses)} connected, {total_tools} tools\n"
        bd = stats.get("token_breakdown", {})
        source_label = " (api)" if stats.get("token_count_source") == "api" else ""
        summary += (
            f"Context: ~{stats['estimated_tokens']:,}{source_label} / {stats['max_tokens']:,} tokens "
            f"({stats['usage_percent']:.1f}%)\n"
            f"  system: {bd.get('system', 0):,}  "
            f"messages: {bd.get('messages', 0):,}  "
            f"tail: {bd.get('tail', 0):,}  "
            f"tools: {bd.get('tools', 0):,}\n"
            f"Session: {self.llm.total_prompt_tokens:,} prompt{self._cached_usage_note()} / "
            f"{self.llm.total_completion_tokens:,} completion tokens"
        )

        if self.skill_manager.get_active_skills():
            summary += "\nActive: " + ", ".join(self.skill_manager.get_active_skills().keys())

        return summary

    def get_current_model(self) -> str:
        """Get current model name.

        Returns:
            Current model name
        """
        return self.llm.model

    def set_provider(
        self,
        api_key: str,
        base_url: Any = _KEEP_BASE_URL,
        model: Optional[str] = None,
        *,
        api_format: Optional[str] = None,
    ) -> None:
        # Implementation lives in ``agentao.runtime.model``. ``base_url``
        # defaults to the keep-current sentinel; an explicit value (incl.
        # ``None``, which clears to the SDK default) replaces the endpoint.
        # ``api_format`` names the new provider's wire protocol (``None``
        # keeps the current one).
        _runtime_model.set_provider(
            self, api_key, base_url=base_url, model=model, api_format=api_format,
        )

    def set_model(self, model: str) -> str:
        # Implementation lives in ``agentao.runtime.model``.
        return _runtime_model.set_model(self, model)

    def list_available_models(self) -> List[str]:
        # Implementation lives in ``agentao.runtime.model``.
        return _runtime_model.list_available_models(self)

    def set_permission_mode(
        self, mode: Union[str, PermissionMode], *, cause: str = "host"
    ) -> Optional[PermissionMode]:
        """Switch the permission posture, and record the transition.

        Prefer this over ``agent.permission_engine.set_mode(mode)``, which
        is only half the switch: ``read-only`` has **two** of them — the
        engine's preset and ``ToolRunner.readonly_mode`` — and the engine
        holds no transport, so a bare ``set_mode`` also emits neither
        ``READONLY_MODE_CHANGED`` nor ``PERMISSION_MODE_CHANGED``, leaving a
        replay file with the resulting denials and no record of the switch.

        ``mode`` is the mode's string value (``"read-only"``,
        ``"workspace-write"``, ``"full-access"``, ``"plan"`` — the vocabulary
        of ``ActivePermissions.mode``) or a ``PermissionMode``. An unknown
        string raises ``ValueError``; another type raises ``TypeError``.

        ``cause`` labels the entry path in the event payload; the default
        suits an embedded host. Returns the previously active mode **as a
        ``PermissionMode``** (unchanged since before strings were accepted),
        and raises ``ValueError`` when this runtime has no permission engine
        (``mode`` is validated first, so a bad mode reports itself either way).

        Implementation lives in ``agentao.runtime.permission_mode``.
        """
        return _runtime_permission_mode.apply_permission_mode(
            self, mode, cause=cause
        )
