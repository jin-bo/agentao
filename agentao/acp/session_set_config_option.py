"""ACP ``session/set_config_option`` handler — model/provider switching.

The single load-bearing security property here: **the wire carries an
identifier, never a secret.** A client switches model/provider by sending
``{sessionId, configId: "model", value: "provider/model"}``; the handler
resolves credentials *server-side* through a host-injectable
``provider_resolver`` and never reads ``apiKey`` / ``baseUrl`` / ``_meta``
off the wire. This is enforced two ways (defense in depth):

  1. **Handler whitelist** — only ``sessionId`` / ``configId`` / ``value``
     are accepted; any other field (notably ``apiKey`` / ``baseUrl`` /
     ``_meta``) makes the request fail with ``INVALID_PARAMS`` rather than
     being silently honoured.
  2. **Schema** — ``AcpSessionSetConfigOptionRequest`` is ``extra="forbid"``.

Value rules (Decision in ``docs/design/deepchat-acp-patch-revision.md``):

  - **Split on the first ``/``** (``partition``, not ``split``): provider ids
    are slash-free, model ids are not (``huggingface/meta-llama/Llama-3`` →
    provider ``huggingface``, model ``meta-llama/Llama-3``).
  - **Bare value (no ``/``)** = model-only switch, keep the current provider.
  - **Same model on different endpoints** = distinct catalog entries
    (``openai/gpt-4o`` vs ``azure-openai/gpt-4o``).

The default ``provider_resolver`` resolves **only** the current
``LLM_PROVIDER`` from the existing factory env (``{PROVIDER}_API_KEY`` /
``_BASE_URL``); any other provider id raises ``INVALID_REQUEST``. It does
**not** scan the environment for a provider list. Multi-provider switching
requires a host-injected resolver paired with a host-injected catalog
(``AcpServer(provider_resolver=..., model_catalog=...)``).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ._handler_utils import (
    hold_idle_turn_lock,
    reject_unexpected_params,
    require_active_session,
)
from .protocol import INVALID_REQUEST, METHOD_SESSION_SET_CONFIG_OPTION
from .server import JsonRpcHandlerError

if TYPE_CHECKING:
    from .models import AcpSessionState
    from .server import AcpServer

logger = logging.getLogger(__name__)

# Only these fields are read off the wire. A whitelist (rather than ignoring
# unknown keys) is the security boundary — a client that puts ``apiKey`` /
# ``baseUrl`` / ``_meta`` on the request is rejected, not silently obliged.
_ALLOWED_KEYS = frozenset({"sessionId", "configId", "value"})

#: The only ``configId`` Agentao supports today.
_CONFIG_ID_MODEL = "model"


# ---------------------------------------------------------------------------
# Default provider resolution + catalog (single-provider, env-sourced)
# ---------------------------------------------------------------------------

def _default_provider_id() -> str:
    """The provider id of the single env-configured provider, lower-cased.

    Mirrors ``factory.discover_llm_kwargs``' ``LLM_PROVIDER`` read but in the
    ``provider/model`` value casing (lower-case) used on the wire. Reads
    ``LLM_PROVIDER`` directly — deliberately NOT via
    ``factory.resolve_provider_name`` (which upper-cases): for non-ASCII names
    whose ``.upper().lower()`` is not idempotent (``ß`` / ``ı`` / ligatures),
    round-tripping would change the wire value and the accept/reject decision
    in :func:`default_provider_resolver`.
    """
    return os.getenv("LLM_PROVIDER", "OPENAI").strip().lower()


def default_provider_resolver(provider_id: str) -> Dict[str, Optional[str]]:
    """Resolve credentials for ``provider_id`` from the process environment.

    Accepts **only** the single configured ``LLM_PROVIDER``. Any other id
    raises ``LookupError`` (the handler maps it to ``INVALID_REQUEST``) — the
    default never scans the environment for a provider list nor fabricates a
    ``{PROVIDER}_*`` lookup for an arbitrary id. Multi-provider switching is a
    host concern (inject ``provider_resolver``).

    Returns ``{"api_key", "base_url"}`` (``base_url`` may be ``None``).
    """
    # Read LLM_PROVIDER directly (not via factory.resolve_provider_name, which
    # upper-cases): the accept/reject comparison below must use the raw value's
    # casefold so a non-ASCII provider name with non-idempotent ``.upper().lower()``
    # (``ß`` / ``ı`` / ligatures) is not silently rejected.
    env_provider = os.getenv("LLM_PROVIDER", "OPENAI").strip()
    if provider_id.strip().lower() != env_provider.lower():
        raise LookupError(
            f"provider {provider_id!r} is not the configured provider "
            f"({env_provider.lower()!r}); inject a provider_resolver to switch "
            "providers"
        )
    prefix = env_provider.upper()
    api_key = os.getenv(f"{prefix}_API_KEY")
    if not api_key:
        raise LookupError(
            f"no API key configured for provider {provider_id!r} "
            f"(expected {prefix}_API_KEY)"
        )
    return {"api_key": api_key, "base_url": os.getenv(f"{prefix}_BASE_URL")}


def _current_provider_id(session: "AcpSessionState") -> str:
    """The provider id the session is currently bound to."""
    return session.provider_id or _default_provider_id()


def _current_model(session: "AcpSessionState") -> str:
    """The session's live model id (read from the LLM client)."""
    return session.agent.llm.model


def _current_value(session: "AcpSessionState") -> str:
    """The ``provider/model`` value that is currently active."""
    return f"{_current_provider_id(session)}/{_current_model(session)}"


def _model_catalog(
    server: "AcpServer", session: "AcpSessionState"
) -> List[Dict[str, Any]]:
    """The ``options`` advertised on the ``model`` config option.

    Host-injected catalog wins. Otherwise the default is a **single** entry —
    the live ``provider/model`` — because Agentao is single-provider today
    (``LLM_PROVIDER`` selects one at construction). A richer catalog must be
    host-injected; the default never scans env or guesses the provider list.
    """
    if server.model_catalog is not None:
        return [dict(opt) for opt in server.model_catalog]
    model = _current_model(session)
    return [{"value": f"{_current_provider_id(session)}/{model}", "name": model}]


def _model_config_option(
    server: "AcpServer", session: "AcpSessionState"
) -> Dict[str, Any]:
    return {
        "id": _CONFIG_ID_MODEL,
        "name": "Model",
        "category": "model",
        "type": "select",
        "currentValue": _current_value(session),
        "options": _model_catalog(server, session),
    }


def config_options_for_session(
    server: "AcpServer", session: "AcpSessionState"
) -> List[Dict[str, Any]]:
    """Build the ``configOptions`` advertised in ``session/new`` / ``load``.

    Never raises: building the catalog must not be able to abort session
    creation (the agent may be a duck-typed fake without a real ``llm``).
    Returns ``[]`` if the option can't be built.
    """
    try:
        return [_model_config_option(server, session)]
    except Exception:
        logger.exception(
            "acp: could not build configOptions for session %s",
            session.session_id,
        )
        return []


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle_session_set_config_option(
    server: "AcpServer", params: Any
) -> Dict[str, Any]:
    session = require_active_session(server, params, METHOD_SESSION_SET_CONFIG_OPTION)

    # Whitelist — the wire never carries credentials. Reject (don't ignore)
    # apiKey / baseUrl / _meta so a misbehaving client fails loudly.
    reject_unexpected_params(
        params,
        _ALLOWED_KEYS,
        METHOD_SESSION_SET_CONFIG_OPTION,
        reason="credentials resolve server-side and never travel on the wire",
    )

    config_id = params.get("configId")
    if config_id != _CONFIG_ID_MODEL:
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=f"unknown configId {config_id!r}; only 'model' is supported",
        )

    value = params.get("value")
    if not isinstance(value, str) or not value.strip():
        raise TypeError(
            f"{METHOD_SESSION_SET_CONFIG_OPTION}.value must be a non-empty string"
        )
    value = value.strip()

    provider_id, sep, model_id = value.partition("/")

    # Holding turn_lock prevents an in-flight session/prompt from observing a
    # model/provider change mid-stream.
    with hold_idle_turn_lock(session, METHOD_SESSION_SET_CONFIG_OPTION):
        if sep:  # "provider/model" form
            # Normalize the provider id to the canonical wire casing
            # (lower-case, trimmed) so ``currentValue`` always matches a
            # canonical catalog entry. Model ids are case-sensitive — only
            # trimmed, never lower-cased.
            provider_id = provider_id.strip().lower()
            model_id = model_id.strip()
            if not provider_id or not model_id:
                raise TypeError(
                    f"{METHOD_SESSION_SET_CONFIG_OPTION}.value 'provider/model' "
                    "must have a non-empty provider and model"
                )
            resolver = server.provider_resolver or default_provider_resolver
            try:
                creds = resolver(provider_id)
            except Exception as e:
                # Unknown / unavailable provider — the resolver signals via
                # any exception. Log only the provider id and exception *type*,
                # never the exception text: a buggy host resolver could embed a
                # key in its message, and this feature promises credentials
                # stay out of logs as well as off the wire. The wire response
                # is likewise generic.
                logger.warning(
                    "acp: provider resolution failed for %r (%s)",
                    provider_id,
                    type(e).__name__,
                )
                raise JsonRpcHandlerError(
                    code=INVALID_REQUEST,
                    message=f"cannot resolve provider {provider_id!r}",
                )
            if not isinstance(creds, dict) or not creds.get("api_key"):
                raise JsonRpcHandlerError(
                    code=INVALID_REQUEST,
                    message=(
                        f"provider_resolver returned no api_key for "
                        f"{provider_id!r}"
                    ),
                )
            # A provider switch replaces the endpoint wholesale: pass the
            # resolved base_url explicitly (``None`` clears it to the SDK
            # default, rather than inheriting the previous provider's custom
            # endpoint — e.g. switching an Azure-style endpoint back to
            # api.openai.com). ``set_provider`` reconfigures the LLM client and
            # emits MODEL_CHANGED with the api_key intentionally excluded from
            # the event payload (so replay files never capture credentials).
            session.agent.set_provider(
                api_key=creds["api_key"],
                base_url=creds.get("base_url"),
                model=model_id,
            )
            session.provider_id = provider_id
        else:  # bare value — model-only switch, keep the current provider
            session.agent.set_model(value)

        return {"configOptions": config_options_for_session(server, session)}


def register(server: "AcpServer") -> None:
    server.register(
        METHOD_SESSION_SET_CONFIG_OPTION,
        lambda params: handle_session_set_config_option(server, params),
    )
