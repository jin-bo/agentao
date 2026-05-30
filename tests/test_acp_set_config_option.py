"""Tests for PR-4 — ACP model/provider switching.

Covers the standard ``session/set_config_option`` (configId="model"), the
vendor ``_agentao.cn/set_model`` free-form method, the default
env-sourced ``provider_resolver``, and the ``configOptions`` advertised in
``session/new`` / ``session/load``.

The load-bearing security property under test: **credentials never travel
on the wire.** A client sends only a ``provider/model`` identifier; the
handler resolves the key server-side and rejects any ``apiKey`` / ``baseUrl``
/ ``_meta`` field.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

import pytest

from agentao.acp import agentao_set_model as acp_vendor_set_model
from agentao.acp import session_set_config_option as acp_set_config
from agentao.acp.models import AcpSessionState
from agentao.acp.protocol import (
    INVALID_REQUEST,
    METHOD_AGENTAO_SET_MODEL,
    METHOD_SESSION_SET_CONFIG_OPTION,
    SERVER_NOT_INITIALIZED,
)
from agentao.acp.server import AcpServer, JsonRpcHandlerError
from agentao.acp.session_set_config_option import (
    config_options_for_session,
    default_provider_resolver,
)
from agentao.llm.client import KEEP_BASE_URL

from .support.acp_server import make_initialized_server, make_server


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, model: str = "gpt-init", base_url: Optional[str] = None) -> None:
        self.model = model
        self.base_url = base_url


class _FakeAgent:
    """Duck-typed agent exposing the model-switch surface the handlers use."""

    def __init__(self, model: str = "gpt-init", base_url: Optional[str] = None) -> None:
        self.llm = _FakeLLM(model=model, base_url=base_url)
        self.set_model_calls: List[str] = []
        self.set_provider_calls: List[Dict[str, Any]] = []

    def set_model(self, model: str) -> str:
        self.set_model_calls.append(model)
        self.llm.model = model
        return f"changed to {model}"

    def set_provider(
        self,
        api_key: str,
        base_url: Any = KEEP_BASE_URL,
        model: Optional[str] = None,
    ) -> None:
        self.set_provider_calls.append(
            {"api_key": api_key, "base_url": base_url, "model": model}
        )
        # Mirror set_provider/reconfigure: the KEEP_BASE_URL sentinel keeps
        # the current endpoint; an explicit value (incl. None) replaces it.
        if base_url is not KEEP_BASE_URL:
            self.llm.base_url = base_url
        if model is not None:
            self.llm.model = model


def _register(server: AcpServer, agent: _FakeAgent, sid: str = "s") -> AcpSessionState:
    state = AcpSessionState(session_id=sid)
    state.agent = agent  # type: ignore[assignment]
    server.sessions.create(state)
    return state


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # A clean, known single-provider env for the default resolver / catalog.
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


# ===========================================================================
# session/set_config_option — guards
# ===========================================================================


class TestSetConfigOptionGuards:
    def test_pre_initialize_raises(self):
        server = make_server()
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_set_config.handle_session_set_config_option(
                server, {"sessionId": "s", "configId": "model", "value": "openai/gpt-4o"}
            )
        assert exc.value.code == SERVER_NOT_INITIALIZED

    def test_unknown_session(self):
        server = make_initialized_server()
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_set_config.handle_session_set_config_option(
                server, {"sessionId": "nope", "configId": "model", "value": "openai/x"}
            )
        assert exc.value.code == INVALID_REQUEST

    def test_unknown_config_id(self):
        server = make_initialized_server()
        _register(server, _FakeAgent())
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_set_config.handle_session_set_config_option(
                server, {"sessionId": "s", "configId": "temperature", "value": "0.5"}
            )
        assert exc.value.code == INVALID_REQUEST
        assert "configId" in exc.value.message

    def test_empty_value_rejected(self):
        server = make_initialized_server()
        _register(server, _FakeAgent())
        with pytest.raises(TypeError):
            acp_set_config.handle_session_set_config_option(
                server, {"sessionId": "s", "configId": "model", "value": ""}
            )

    def test_trailing_slash_value_rejected(self):
        server = make_initialized_server()
        _register(server, _FakeAgent())
        with pytest.raises(TypeError):
            acp_set_config.handle_session_set_config_option(
                server, {"sessionId": "s", "configId": "model", "value": "openai/"}
            )

    @pytest.mark.parametrize("secret_field", ["apiKey", "baseUrl", "_meta"])
    def test_credential_fields_rejected(self, secret_field):
        # The wire never carries credentials — a request with apiKey/baseUrl/
        # _meta must fail loudly rather than be silently honoured.
        server = make_initialized_server()
        _register(server, _FakeAgent())
        with pytest.raises(TypeError) as exc:
            acp_set_config.handle_session_set_config_option(
                server,
                {
                    "sessionId": "s",
                    "configId": "model",
                    "value": "openai/gpt-4o",
                    secret_field: "leak",
                },
            )
        assert secret_field in str(exc.value)

    def test_busy_turn_rejected(self):
        server = make_initialized_server()
        state = _register(server, _FakeAgent())
        state.turn_lock.acquire()
        try:
            with pytest.raises(JsonRpcHandlerError) as exc:
                acp_set_config.handle_session_set_config_option(
                    server,
                    {"sessionId": "s", "configId": "model", "value": "openai/gpt-4o"},
                )
            assert exc.value.code == INVALID_REQUEST
        finally:
            state.turn_lock.release()


# ===========================================================================
# session/set_config_option — switching behavior
# ===========================================================================


class TestSetConfigOptionSwitch:
    def test_provider_model_switch_resolves_credentials_serverside(self):
        server = make_initialized_server()
        agent = _FakeAgent()
        _register(server, agent)

        result = acp_set_config.handle_session_set_config_option(
            server, {"sessionId": "s", "configId": "model", "value": "openai/gpt-4o"}
        )

        # Credentials came from the server-side env, not the wire.
        assert agent.set_provider_calls == [
            {
                "api_key": "sk-secret",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
            }
        ]
        assert agent.llm.model == "gpt-4o"
        opt = result["configOptions"][0]
        assert opt["currentValue"] == "openai/gpt-4o"

    def test_model_with_slashes_splits_on_first_slash(self):
        # huggingface/meta-llama/Llama-3 → provider huggingface, model
        # meta-llama/Llama-3 (split on the FIRST slash).
        server = make_initialized_server()
        agent = _FakeAgent()
        _register(server, agent)
        captured: Dict[str, Any] = {}

        def resolver(provider_id: str) -> Dict[str, Optional[str]]:
            captured["provider"] = provider_id
            return {"api_key": "k", "base_url": None}

        server.provider_resolver = resolver
        acp_set_config.handle_session_set_config_option(
            server,
            {
                "sessionId": "s",
                "configId": "model",
                "value": "huggingface/meta-llama/Llama-3",
            },
        )
        assert captured["provider"] == "huggingface"
        assert agent.set_provider_calls[0]["model"] == "meta-llama/Llama-3"

    def test_bare_value_is_model_only_keeps_provider(self):
        server = make_initialized_server()
        agent = _FakeAgent()
        _register(server, agent)

        result = acp_set_config.handle_session_set_config_option(
            server, {"sessionId": "s", "configId": "model", "value": "gpt-4o-mini"}
        )
        assert agent.set_model_calls == ["gpt-4o-mini"]
        assert agent.set_provider_calls == []
        assert agent.llm.model == "gpt-4o-mini"
        # Provider unchanged → currentValue keeps the default provider prefix.
        assert result["configOptions"][0]["currentValue"] == "openai/gpt-4o-mini"

    def test_provider_id_normalized_case_and_whitespace(self):
        # "  OpenAI  / gpt-4o " → provider normalized to "openai" (trimmed,
        # lower-cased), model trimmed to "gpt-4o" (case preserved). The
        # advertised currentValue is the canonical "openai/gpt-4o".
        server = make_initialized_server()
        agent = _FakeAgent()
        _register(server, agent)

        result = acp_set_config.handle_session_set_config_option(
            server, {"sessionId": "s", "configId": "model", "value": "  OpenAI  / gpt-4o "}
        )
        assert agent.set_provider_calls[0]["model"] == "gpt-4o"
        assert result["configOptions"][0]["currentValue"] == "openai/gpt-4o"

    def test_provider_switch_clears_stale_base_url(self):
        # Switching to a provider whose resolver returns no base_url must
        # CLEAR the previous endpoint (not keep it), or requests would keep
        # hitting the old provider's URL with the new key/model.
        server = make_initialized_server()
        agent = _FakeAgent(base_url="https://azure.example/openai")
        _register(server, agent)
        server.provider_resolver = lambda pid: {"api_key": "k", "base_url": None}

        acp_set_config.handle_session_set_config_option(
            server, {"sessionId": "s", "configId": "model", "value": "openai/gpt-4o"}
        )
        assert agent.llm.base_url is None

    def test_provider_switch_applies_new_base_url(self):
        server = make_initialized_server()
        agent = _FakeAgent(base_url="https://old.example")
        _register(server, agent)
        server.provider_resolver = lambda pid: {
            "api_key": "k",
            "base_url": "https://new.example",
        }
        acp_set_config.handle_session_set_config_option(
            server, {"sessionId": "s", "configId": "model", "value": "custom/m"}
        )
        assert agent.llm.base_url == "https://new.example"

    def test_unknown_provider_maps_to_invalid_request(self):
        server = make_initialized_server()
        _register(server, _FakeAgent())
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_set_config.handle_session_set_config_option(
                server, {"sessionId": "s", "configId": "model", "value": "azure/gpt-4o"}
            )
        assert exc.value.code == INVALID_REQUEST

    def test_host_resolver_returning_no_key_rejected(self):
        server = make_initialized_server()
        _register(server, _FakeAgent())
        server.provider_resolver = lambda pid: {"base_url": "x"}  # no api_key
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_set_config.handle_session_set_config_option(
                server, {"sessionId": "s", "configId": "model", "value": "openai/gpt-4o"}
            )
        assert exc.value.code == INVALID_REQUEST


# ===========================================================================
# config_options_for_session / catalog
# ===========================================================================


class TestConfigOptions:
    def test_default_single_entry(self):
        server = make_initialized_server()
        state = _register(server, _FakeAgent(model="gpt-init"))
        opts = config_options_for_session(server, state)
        assert opts == [
            {
                "id": "model",
                "name": "Model",
                "category": "model",
                "type": "select",
                "currentValue": "openai/gpt-init",
                "options": [{"value": "openai/gpt-init", "name": "gpt-init"}],
            }
        ]

    def test_host_injected_catalog_wins(self):
        catalog = [
            {"value": "openai/gpt-4o", "name": "GPT-4o"},
            {"value": "anthropic/claude-opus-4", "name": "Claude Opus 4"},
        ]
        server = make_initialized_server()
        server.model_catalog = catalog
        state = _register(server, _FakeAgent(model="gpt-init"))
        opts = config_options_for_session(server, state)
        assert opts[0]["options"] == catalog
        # A mutation of the returned catalog must not corrupt server state.
        opts[0]["options"][0]["name"] = "MUTATED"
        assert server.model_catalog[0]["name"] == "GPT-4o"

    def test_defensive_empty_when_agent_has_no_llm(self):
        class _Bare:
            pass

        server = make_initialized_server()
        state = AcpSessionState(session_id="b")
        state.agent = _Bare()  # type: ignore[assignment]
        server.sessions.create(state)
        assert config_options_for_session(server, state) == []


# ===========================================================================
# _agentao.cn/set_model — vendor free-form
# ===========================================================================


class TestVendorSetModel:
    def test_happy_path(self):
        server = make_initialized_server()
        agent = _FakeAgent()
        _register(server, agent)
        result = acp_vendor_set_model.handle_agentao_set_model(
            server, {"sessionId": "s", "model": "any-model-string"}
        )
        assert agent.set_model_calls == ["any-model-string"]
        assert result == {"model": "any-model-string"}

    def test_pre_initialize_raises(self):
        server = make_server()
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_vendor_set_model.handle_agentao_set_model(
                server, {"sessionId": "s", "model": "x"}
            )
        assert exc.value.code == SERVER_NOT_INITIALIZED

    def test_empty_model_rejected(self):
        server = make_initialized_server()
        _register(server, _FakeAgent())
        with pytest.raises(TypeError):
            acp_vendor_set_model.handle_agentao_set_model(
                server, {"sessionId": "s", "model": ""}
            )

    @pytest.mark.parametrize("secret_field", ["apiKey", "baseUrl", "_meta", "provider"])
    def test_extra_fields_rejected(self, secret_field):
        server = make_initialized_server()
        _register(server, _FakeAgent())
        with pytest.raises(TypeError):
            acp_vendor_set_model.handle_agentao_set_model(
                server,
                {"sessionId": "s", "model": "x", secret_field: "leak"},
            )


# ===========================================================================
# default_provider_resolver
# ===========================================================================


class TestDefaultProviderResolver:
    def test_resolves_current_provider(self):
        creds = default_provider_resolver("openai")
        assert creds["api_key"] == "sk-secret"
        assert creds["base_url"] == "https://api.openai.com/v1"

    def test_case_insensitive_provider_match(self):
        assert default_provider_resolver("OpenAI")["api_key"] == "sk-secret"

    def test_other_provider_rejected(self):
        with pytest.raises(LookupError):
            default_provider_resolver("anthropic")

    def test_missing_api_key_rejected(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(LookupError):
            default_provider_resolver("openai")

    def test_no_env_scan_for_provider_list(self, monkeypatch):
        # A planted ANTHROPIC_API_KEY must NOT make the default resolver
        # accept "anthropic" — it resolves ONLY the configured LLM_PROVIDER.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-other")
        with pytest.raises(LookupError):
            default_provider_resolver("anthropic")


# ===========================================================================
# AcpServer wiring
# ===========================================================================


def test_server_accepts_seam_kwargs():
    import io

    def resolver(pid: str) -> Dict[str, Optional[str]]:
        return {"api_key": "k"}

    catalog = [{"value": "openai/gpt-4o", "name": "GPT-4o"}]
    server = AcpServer(
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        provider_resolver=resolver,
        model_catalog=catalog,
    )
    assert server.provider_resolver is resolver
    assert server.model_catalog == catalog


def test_server_seams_default_to_none():
    import io

    server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
    assert server.provider_resolver is None
    assert server.model_catalog is None
