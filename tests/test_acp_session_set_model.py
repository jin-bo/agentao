"""Tests for P3 of ACP_STDIO_AUTH_FIX_PLAN.

Covers ``session/set_model``, ``session/set_mode``, and
``session/list_models``. Uses lightweight fakes rather than a real
``Agentao`` runtime — the handlers only duck-type the agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

import pytest

from agentao.acp import session_list_models as acp_list_models
from agentao.acp import session_set_mode as acp_set_mode
from agentao.acp import session_set_model as acp_set_model
from agentao.acp.models import AcpSessionState
from agentao.acp.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_SESSION_LIST_MODELS,
    METHOD_SESSION_SET_MODE,
    METHOD_SESSION_SET_MODEL,
    SERVER_NOT_INITIALIZED,
)
from agentao.acp.server import JsonRpcHandlerError
from agentao.permissions import PermissionEngine, PermissionMode

from .support.acp_server import make_initialized_server, make_server


@pytest.fixture
def make_engine(tmp_path_factory):
    """Factory that returns fresh ``PermissionEngine`` instances.

    The handler tests don't care about rule contents — only that the
    engine is constructable and modal — so each call produces a fresh
    pytest-managed project root with no inherited rules. Returning a
    factory (rather than a single engine) lets tests build multiple
    independent engines for isolation assertions.
    """
    def _make() -> PermissionEngine:
        return PermissionEngine(project_root=tmp_path_factory.mktemp("permissions"))
    return _make


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, model: str = "gpt-init", max_tokens: int = 4096):
        self.model = model
        self.max_tokens = max_tokens


class _FakeContextManager:
    def __init__(self, max_tokens: int = 200_000):
        self.max_tokens = max_tokens
        self._encoding = None
        self._last_api_prompt_tokens = None


class _FakeAgent:
    def __init__(
        self,
        *,
        model: str = "gpt-init",
        llm_max_tokens: int = 4096,
        ctx_max_tokens: int = 200_000,
        models: Optional[List[str]] = None,
        list_error: Optional[Exception] = None,
        permission_engine: Optional[PermissionEngine] = None,
    ):
        self.llm = _FakeLLM(model=model, max_tokens=llm_max_tokens)
        self.context_manager = _FakeContextManager(max_tokens=ctx_max_tokens)
        self.permission_engine = permission_engine
        self._models = models if models is not None else ["a", "b", "c"]
        self._list_error = list_error
        self.set_model_calls: List[str] = []

    # API surface used by the handlers
    def set_model(self, model: str) -> str:
        self.set_model_calls.append(model)
        self.llm.model = model
        return f"changed to {model}"

    def list_available_models(self) -> List[str]:
        if self._list_error is not None:
            raise self._list_error
        return list(self._models)


def _register_session(server, session_id: str, agent: _FakeAgent) -> AcpSessionState:
    state = AcpSessionState(session_id=session_id)
    state.agent = agent  # type: ignore[assignment]
    server.sessions.create(state)
    return state


# ===========================================================================
# session/set_model
# ===========================================================================


class TestSetModelGuards:
    def test_pre_initialize_raises(self):
        server = make_server()
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_set_model.handle_session_set_model(server, {"sessionId": "s", "model": "x"})
        assert exc.value.code == SERVER_NOT_INITIALIZED

    def test_params_must_be_dict(self):
        server = make_initialized_server()
        with pytest.raises(TypeError):
            acp_set_model.handle_session_set_model(server, [])

    def test_missing_session_id(self):
        server = make_initialized_server()
        with pytest.raises(TypeError):
            acp_set_model.handle_session_set_model(server, {"model": "x"})

    def test_unknown_session(self):
        server = make_initialized_server()
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_set_model.handle_session_set_model(
                server, {"sessionId": "nope", "model": "x"}
            )
        assert exc.value.code == INVALID_REQUEST

    def test_no_fields_raises(self):
        server = make_initialized_server()
        _register_session(server, "s", _FakeAgent())
        with pytest.raises(TypeError, match="at least one"):
            acp_set_model.handle_session_set_model(server, {"sessionId": "s"})

    def test_negative_context_length_rejected(self):
        server = make_initialized_server()
        _register_session(server, "s", _FakeAgent())
        with pytest.raises(TypeError):
            acp_set_model.handle_session_set_model(
                server, {"sessionId": "s", "contextLength": 0}
            )

    def test_bool_max_tokens_rejected(self):
        server = make_initialized_server()
        _register_session(server, "s", _FakeAgent())
        with pytest.raises(TypeError):
            acp_set_model.handle_session_set_model(
                server, {"sessionId": "s", "maxTokens": True}
            )


class TestSetModelKnobsAreIndependent:
    def test_only_model_does_not_reset_token_caps(self):
        server = make_initialized_server()
        agent = _FakeAgent(llm_max_tokens=1234, ctx_max_tokens=99_999)
        _register_session(server, "s", agent)

        result = acp_set_model.handle_session_set_model(
            server, {"sessionId": "s", "model": "gpt-new"}
        )
        assert agent.llm.model == "gpt-new"
        assert agent.llm.max_tokens == 1234, "maxTokens must not be touched"
        assert agent.context_manager.max_tokens == 99_999, (
            "contextLength must not be touched"
        )
        assert result == {
            "model": "gpt-new",
            "contextLength": 99_999,
            "maxTokens": 1234,
        }

    def test_context_length_only_writes_to_context_manager(self):
        server = make_initialized_server()
        agent = _FakeAgent(llm_max_tokens=5000, ctx_max_tokens=200_000)
        _register_session(server, "s", agent)

        acp_set_model.handle_session_set_model(
            server, {"sessionId": "s", "contextLength": 500_000}
        )
        assert agent.context_manager.max_tokens == 500_000
        assert agent.llm.max_tokens == 5000, (
            "contextLength must NOT collapse the per-request completion cap"
        )

    def test_max_tokens_only_writes_to_llm(self):
        server = make_initialized_server()
        agent = _FakeAgent(llm_max_tokens=4096, ctx_max_tokens=200_000)
        _register_session(server, "s", agent)

        acp_set_model.handle_session_set_model(
            server, {"sessionId": "s", "maxTokens": 8192}
        )
        assert agent.llm.max_tokens == 8192
        assert agent.context_manager.max_tokens == 200_000, (
            "maxTokens must NOT touch the context window"
        )

    def test_all_three_together_apply_separately(self):
        """The critical anti-regression: a single request carrying all
        three fields lands them on three distinct attributes — never
        cross-pollinating maxTokens into the context window."""
        server = make_initialized_server()
        agent = _FakeAgent(
            model="gpt-init", llm_max_tokens=4096, ctx_max_tokens=200_000
        )
        _register_session(server, "s", agent)

        result = acp_set_model.handle_session_set_model(
            server,
            {
                "sessionId": "s",
                "model": "gpt-new",
                "contextLength": 1_000_000,
                "maxTokens": 16_000,
            },
        )
        assert agent.llm.model == "gpt-new"
        assert agent.context_manager.max_tokens == 1_000_000
        assert agent.llm.max_tokens == 16_000
        assert result == {
            "model": "gpt-new",
            "contextLength": 1_000_000,
            "maxTokens": 16_000,
        }


# ===========================================================================
# session/set_mode
# ===========================================================================


class TestSetMode:
    def test_pre_initialize_raises(self):
        server = make_server()
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_set_mode.handle_session_set_mode(
                server, {"sessionId": "s", "mode": "read-only"}
            )
        assert exc.value.code == SERVER_NOT_INITIALIZED

    def test_unknown_mode_rejected(self, make_engine):
        server = make_initialized_server()
        agent = _FakeAgent(permission_engine=make_engine())
        _register_session(server, "s", agent)
        with pytest.raises(TypeError):
            acp_set_mode.handle_session_set_mode(
                server, {"sessionId": "s", "mode": "yolo"}
            )

    def test_unknown_session(self):
        server = make_initialized_server()
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_set_mode.handle_session_set_mode(
                server, {"sessionId": "nope", "mode": "read-only"}
            )
        assert exc.value.code == INVALID_REQUEST

    def test_applies_mode_to_session_engine(self, make_engine):
        server = make_initialized_server()
        engine = make_engine()
        agent = _FakeAgent(permission_engine=engine)
        _register_session(server, "s", agent)

        result = acp_set_mode.handle_session_set_mode(
            server, {"sessionId": "s", "mode": "read-only"}
        )
        assert result == {"mode": "read-only"}
        assert engine.active_mode == PermissionMode.READ_ONLY

    def test_does_not_affect_other_session(self, make_engine):
        """Critical isolation guarantee: each session owns its own
        PermissionEngine; updating session A must not change session B."""
        server = make_initialized_server()
        engine_a = make_engine()
        engine_b = make_engine()
        _register_session(server, "a", _FakeAgent(permission_engine=engine_a))
        _register_session(server, "b", _FakeAgent(permission_engine=engine_b))

        acp_set_mode.handle_session_set_mode(
            server, {"sessionId": "a", "mode": "read-only"}
        )
        assert engine_a.active_mode == PermissionMode.READ_ONLY
        assert engine_b.active_mode == PermissionMode.WORKSPACE_WRITE


# ===========================================================================
# session/list_models
# ===========================================================================


class TestListModels:
    def test_pre_initialize_raises(self):
        server = make_server()
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_list_models.handle_session_list_models(server, {"sessionId": "s"})
        assert exc.value.code == SERVER_NOT_INITIALIZED

    def test_unknown_session(self):
        server = make_initialized_server()
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_list_models.handle_session_list_models(server, {"sessionId": "nope"})
        assert exc.value.code == INVALID_REQUEST

    def test_returns_models_on_success(self):
        server = make_initialized_server()
        agent = _FakeAgent(models=["m1", "m2", "m3"])
        _register_session(server, "s", agent)

        result = acp_list_models.handle_session_list_models(
            server, {"sessionId": "s"}
        )
        assert result == {"models": ["m1", "m2", "m3"]}

    def test_failure_returns_empty_with_warning_when_no_cache(self):
        server = make_initialized_server()
        agent = _FakeAgent(list_error=RuntimeError("provider down"))
        _register_session(server, "s", agent)

        result = acp_list_models.handle_session_list_models(
            server, {"sessionId": "s"}
        )
        assert result["models"] == []
        assert "warning" in result
        assert "provider down" in result["warning"]

    def test_failure_returns_cached_list_with_warning(self):
        server = make_initialized_server()
        agent = _FakeAgent(models=["m1", "m2"])
        _register_session(server, "s", agent)

        # First call populates the cache.
        first = acp_list_models.handle_session_list_models(
            server, {"sessionId": "s"}
        )
        assert first["models"] == ["m1", "m2"]

        # Now break the provider.
        agent._list_error = RuntimeError("network error")
        second = acp_list_models.handle_session_list_models(
            server, {"sessionId": "s"}
        )
        assert second["models"] == ["m1", "m2"]
        assert "warning" in second

    def test_cache_is_per_session(self):
        server = make_initialized_server()
        agent_a = _FakeAgent(models=["a1", "a2"])
        agent_b = _FakeAgent(models=["b1"])
        _register_session(server, "a", agent_a)
        _register_session(server, "b", agent_b)

        ra = acp_list_models.handle_session_list_models(server, {"sessionId": "a"})
        rb = acp_list_models.handle_session_list_models(server, {"sessionId": "b"})
        assert ra == {"models": ["a1", "a2"]}
        assert rb == {"models": ["b1"]}

        # Break only B; A's cache should be untouched and still served.
        agent_a._list_error = RuntimeError("a-down")
        agent_b._list_error = RuntimeError("b-down")
        ra2 = acp_list_models.handle_session_list_models(server, {"sessionId": "a"})
        rb2 = acp_list_models.handle_session_list_models(server, {"sessionId": "b"})
        assert ra2["models"] == ["a1", "a2"]
        assert rb2["models"] == ["b1"]


# ===========================================================================
# Registration smoke tests
# ===========================================================================


def test_register_set_model():
    server = make_initialized_server()
    acp_set_model.register(server)
    assert METHOD_SESSION_SET_MODEL in server._handlers


def test_register_set_mode():
    server = make_initialized_server()
    acp_set_mode.register(server)
    assert METHOD_SESSION_SET_MODE in server._handlers


def test_register_list_models():
    server = make_initialized_server()
    acp_list_models.register(server)
    assert METHOD_SESSION_LIST_MODELS in server._handlers


def test_unregistered_methods_return_method_not_found():
    """Sanity: before registration, set_model is genuinely missing — this
    is the symptom users hit before the fix."""
    server = make_initialized_server()
    assert METHOD_SESSION_SET_MODEL not in server._handlers
    assert METHOD_SESSION_SET_MODE not in server._handlers
    assert METHOD_SESSION_LIST_MODELS not in server._handlers


# ===========================================================================
# Concurrency guard — set_model / set_mode reject during an active turn
# ===========================================================================


class TestActiveTurnGuard:
    """Mutating handlers must not race an in-flight session/prompt.

    The ACP dispatcher runs requests on a worker pool, so without a guard
    a model swap could land mid-stream. ``hold_idle_turn_lock`` mirrors
    the pattern from ``session_prompt.py:182``: non-blocking acquire,
    reject with ``INVALID_REQUEST`` if the lock is held.
    """

    def test_set_model_rejected_while_turn_active(self):
        server = make_initialized_server()
        agent = _FakeAgent()
        state = _register_session(server, "s", agent)
        # Simulate session_prompt holding the lock for an in-flight turn.
        assert state.turn_lock.acquire(blocking=False)
        try:
            with pytest.raises(JsonRpcHandlerError) as exc:
                acp_set_model.handle_session_set_model(
                    server, {"sessionId": "s", "model": "gpt-new"}
                )
            assert exc.value.code == INVALID_REQUEST
            assert "active turn" in exc.value.message
            # Critical: the rejected call must not have mutated anything.
            assert agent.set_model_calls == []
            assert agent.llm.model == "gpt-init"
        finally:
            state.turn_lock.release()

    def test_set_model_succeeds_after_turn_releases(self):
        server = make_initialized_server()
        agent = _FakeAgent()
        state = _register_session(server, "s", agent)
        assert state.turn_lock.acquire(blocking=False)
        state.turn_lock.release()  # turn ended

        result = acp_set_model.handle_session_set_model(
            server, {"sessionId": "s", "model": "gpt-new"}
        )
        assert result["model"] == "gpt-new"
        # Lock is released after a successful update — next turn can run.
        assert state.turn_lock.acquire(blocking=False)
        state.turn_lock.release()

    def test_set_mode_rejected_while_turn_active(self, make_engine):
        server = make_initialized_server()
        engine = make_engine()
        state = _register_session(server, "s", _FakeAgent(permission_engine=engine))
        assert state.turn_lock.acquire(blocking=False)
        try:
            with pytest.raises(JsonRpcHandlerError) as exc:
                acp_set_mode.handle_session_set_mode(
                    server, {"sessionId": "s", "mode": "read-only"}
                )
            assert exc.value.code == INVALID_REQUEST
            assert "active turn" in exc.value.message
            # Mode unchanged.
            assert engine.active_mode == PermissionMode.WORKSPACE_WRITE
        finally:
            state.turn_lock.release()

    def test_list_models_runs_during_active_turn(self):
        """list_models is read-only — no lock required, so an in-flight
        turn must not block catalog refreshes."""
        server = make_initialized_server()
        agent = _FakeAgent(models=["m1", "m2"])
        state = _register_session(server, "s", agent)
        assert state.turn_lock.acquire(blocking=False)
        try:
            result = acp_list_models.handle_session_list_models(
                server, {"sessionId": "s"}
            )
            assert result == {"models": ["m1", "m2"]}
        finally:
            state.turn_lock.release()

    def test_lock_released_on_handler_failure(self):
        """If the mutation block raises, the lock must still release so a
        retry — or the next session/prompt — can proceed."""
        server = make_initialized_server()
        agent = _FakeAgent()
        # Make set_model raise mid-update.
        def boom(_model):
            raise RuntimeError("simulated failure")
        agent.set_model = boom  # type: ignore[assignment]
        state = _register_session(server, "s", agent)

        with pytest.raises(RuntimeError, match="simulated failure"):
            acp_set_model.handle_session_set_model(
                server, {"sessionId": "s", "model": "gpt-new"}
            )

        # Lock must be free again.
        assert state.turn_lock.acquire(blocking=False)
        state.turn_lock.release()
