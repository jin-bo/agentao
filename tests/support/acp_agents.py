"""Shared Agentao test doubles and agent-factory helpers for ACP tests.

All real ``Agentao`` construction pulls in the LLM/tool stack and needs
provider credentials. ACP handlers only duck-type the runtime, so a
small ``FakeAgent`` with ``.chat`` and ``.close`` is enough.

Two design choices worth calling out:

* ``FakeAgent`` unconditionally implements ``.chat`` (so callers that
  only care about ``close`` pay nothing), but ``track_messages`` is
  **off by default**. The ``session_load`` tests assert that the agent's
  ``messages`` list was populated by the loader and *not* mutated by a
  subsequent ``chat`` — if this defaulted to True those tests would
  regress. Multi-session tests that assert message isolation opt in.
* ``ExplodingAgent`` takes the exception as a constructor arg. Historical
  inline copies raised different messages (``"simulated MCP teardown
  failure"`` vs ``"simulated MCP disconnect failure"``) but no test
  asserts on the string — so a single configurable default is fine.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from agentao.cancellation import CancellationToken


class FakeAgent:
    """Minimal ``Agentao`` replacement.

    Attributes:
        reply: Return value of ``chat`` on the non-cancelled path.
        side_effect: Optional callable run inside ``chat`` before the
            reply is returned. Receives the ``CancellationToken`` so
            cancellation tests can flip it mid-call.
        track_messages: When True, ``chat`` appends ``{"role": "user", ...}``
            and ``{"role": "assistant", ...}`` entries to ``self.messages``
            so tests can observe per-session message history isolation.
            Defaults to False so ``session_load`` tests — which hydrate
            ``messages`` externally and then assert it stays equal to the
            saved history — are not disturbed.
        messages: Populated externally by ``session_load`` hydration, or
            by ``chat`` when ``track_messages`` is True.
        chat_calls: ``(user_message, cancellation_token)`` tuples.
        close_calls: Incremented on each ``close`` call.
    """

    def __init__(
        self,
        reply: str = "ok",
        *,
        side_effect: Optional[Callable[[CancellationToken], None]] = None,
        track_messages: bool = False,
    ) -> None:
        self.reply = reply
        self.side_effect = side_effect
        self.track_messages = track_messages
        self.messages: List[Dict[str, Any]] = []
        self.chat_calls: List[Tuple[str, CancellationToken]] = []
        self.close_calls = 0

    def chat(
        self,
        user_message: str,
        max_iterations: int = 100,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> str:
        assert cancellation_token is not None, "handler must always pass a token"
        self.chat_calls.append((user_message, cancellation_token))
        if self.track_messages:
            self.messages.append({"role": "user", "content": user_message})
        if self.side_effect is not None:
            self.side_effect(cancellation_token)
        if cancellation_token.is_cancelled:
            return "[Cancelled: acp]"
        if self.track_messages:
            self.messages.append({"role": "assistant", "content": self.reply})
        return self.reply

    def close(self) -> None:
        self.close_calls += 1


class ExplodingAgent(FakeAgent):
    """Fake agent whose ``close`` raises — exercises shutdown robustness."""

    def __init__(
        self,
        *args: Any,
        exc: Optional[BaseException] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._exc = exc if exc is not None else RuntimeError("simulated close failure")

    def close(self) -> None:
        self.close_calls += 1
        raise self._exc


class StallingFakeAgent:
    """Fake agent whose ``chat`` blocks on a barrier and polls the token.

    Mirrors the real agent loop's "check token between iterations"
    pattern: sets ``entered`` so the driving thread can synchronize on
    "chat is now in flight", then loops until either the token is
    cancelled or ``release`` fires. The 5s deadline is a safety net so
    a broken test cannot hang CI forever.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.observed_cancellation = False
        self.chat_calls = 0
        self.close_calls = 0

    def chat(
        self,
        user_message: str,
        max_iterations: int = 100,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> str:
        self.chat_calls += 1
        assert cancellation_token is not None
        self.entered.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if cancellation_token.is_cancelled:
                self.observed_cancellation = True
                return "[Cancelled: acp]"
            if self.release.is_set():
                return "released"
            time.sleep(0.005)
        return "timeout"

    def close(self) -> None:
        self.close_calls += 1


def make_factory(agent: Any) -> Callable[..., Any]:
    """Return an agent factory that always yields ``agent``."""

    def factory(**kwargs: Any) -> Any:
        return agent

    return factory


def make_round_robin_factory(agents: List[Any]) -> Callable[..., Any]:
    """Return a factory that yields each element of ``agents`` in turn.

    Raises :class:`AssertionError` if called more times than there are
    agents — a test that over-runs the pool has an unexpected code path.
    """
    iterator = iter(agents)

    def factory(**kwargs: Any) -> Any:
        try:
            return next(iterator)
        except StopIteration as e:
            raise AssertionError("agent factory exhausted") from e

    return factory


def make_recording_factory() -> Tuple[Callable[..., FakeAgent], List[Dict[str, Any]]]:
    """Return ``(factory, calls)`` — the factory appends its kwargs to
    ``calls`` on each invocation so tests can assert the handler passed
    the expected arguments.
    """
    calls: List[Dict[str, Any]] = []

    def factory(**kwargs: Any) -> FakeAgent:
        calls.append(kwargs)
        return FakeAgent()

    return factory, calls


def make_failing_factory(exc: BaseException) -> Callable[..., Any]:
    """Return a factory that raises ``exc`` on every call."""

    def factory(**kwargs: Any) -> Any:
        raise exc

    return factory
