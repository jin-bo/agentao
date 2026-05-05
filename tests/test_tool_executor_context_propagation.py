"""Verifies the ToolExecutor propagates parent-thread ContextVar state to
parallel workers, and that worker mutations stay isolated from the parent.

The contract has two load-bearing assertions: positive propagation
(worker reads what the parent set) and isolation (worker writes do not
leak back). A single-positive test could pass even with the wrong
placement on certain GIL orderings — the isolation assertion is what
catches an incorrect implementation that uses a shared Context.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any, Dict, List

from agentao.runtime.tool_executor import ToolExecutor
from agentao.tools import Tool

from tests.support.host_events import NullTransport, make_plan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_TEST_VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentao_test_propagation_var", default="<unset>",
)


class _ContextReader(Tool):
    """Reads ``_TEST_VAR`` inside its execute thread.

    Records the observed value so the test can assert propagation onto
    the worker thread.
    """

    def __init__(self, observed: List[str]) -> None:
        self._observed = observed

    @property
    def name(self) -> str:
        return "ctx_reader"

    @property
    def description(self) -> str:
        return "reads contextvar"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object"}

    def execute(self, **_kwargs) -> str:
        self._observed.append(_TEST_VAR.get())
        return "ok"


class _ContextMutator(Tool):
    """Writes ``_TEST_VAR`` inside its execute thread.

    Used to verify that a worker mutation does NOT leak back into the
    parent thread (Context.run isolation contract).
    """

    def __init__(self, value: str) -> None:
        self._value = value

    @property
    def name(self) -> str:
        return "ctx_mutator"

    @property
    def description(self) -> str:
        return "writes contextvar"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object"}

    def execute(self, **_kwargs) -> str:
        _TEST_VAR.set(self._value)
        return "ok"


def _make_executor() -> ToolExecutor:
    return ToolExecutor(
        NullTransport(),
        logging.getLogger("test.tool_executor_context"),
        sandbox_policy=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _run_in_fresh_context(callable_, *args, **kwargs):
    """Run ``callable_`` inside a fresh contextvars.Context so a test's
    set() does not leak into other tests' worker threads.
    """
    ctx = contextvars.copy_context()
    return ctx.run(callable_, *args, **kwargs)


def test_parent_contextvar_propagates_to_parallel_workers():
    """A value set on the parent thread must be visible inside workers.

    Multiple plans force the ThreadPoolExecutor branch (single-tool fast
    path bypasses the pool entirely).
    """
    observed: List[str] = []
    executor = _make_executor()

    def _drive():
        _TEST_VAR.set("parent-value")
        plans = [
            make_plan(_ContextReader(observed), call_id="r-1"),
            make_plan(_ContextReader(observed), call_id="r-2"),
        ]
        executor.execute_batch(plans)

    _run_in_fresh_context(_drive)

    assert observed == ["parent-value", "parent-value"], observed


def test_worker_mutations_do_not_leak_into_parent_context():
    """A worker's set() must mutate only its own Context copy.

    This is the load-bearing assertion: an implementation that submits
    the parent's Context object directly (or otherwise shares state)
    would let the parent observe the worker's write. Each plan must run
    inside an independent ``copy_context()`` snapshot.
    """
    executor = _make_executor()
    parent_after: Dict[str, str] = {}

    def _drive():
        _TEST_VAR.set("parent-value")
        plans = [
            make_plan(_ContextMutator("worker-A"), call_id="m-1"),
            make_plan(_ContextMutator("worker-B"), call_id="m-2"),
        ]
        executor.execute_batch(plans)
        parent_after["value"] = _TEST_VAR.get()

    _run_in_fresh_context(_drive)

    assert parent_after["value"] == "parent-value", (
        f"worker write leaked back into parent context: {parent_after['value']!r}"
    )


def test_each_worker_gets_independent_context_copy():
    """Two parallel workers writing different values must not stomp each
    other. With a shared Context, the second .run() would raise
    RuntimeError ("cannot enter context: already entered") and
    execute_batch would surface an error result.
    """
    observed: List[str] = []
    executor = _make_executor()

    class _Probe(Tool):
        @property
        def name(self) -> str:
            return "probe"

        @property
        def description(self) -> str:
            return "set then read"

        @property
        def parameters(self) -> Dict[str, Any]:
            return {"type": "object"}

        def execute(self, **kwargs) -> str:
            value = kwargs.get("v", "")
            _TEST_VAR.set(value)
            observed.append(_TEST_VAR.get())
            return "ok"

    def _drive():
        plans = []
        for i in range(4):
            plan = make_plan(_Probe(), call_id=f"p-{i}")
            plan.function_args = {"v": f"v-{i}"}
            plans.append(plan)
        results = executor.execute_batch(plans)
        # No worker observed an "already-entered" RuntimeError.
        for _cid, info in results.items():
            assert info.status == "ok", info.result

    _run_in_fresh_context(_drive)
    # Every worker saw the value it wrote (no shared-context interference).
    assert sorted(observed) == [f"v-{i}" for i in range(4)]
