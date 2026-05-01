"""P0.7 regression: agentao must not pollute the host's logging stack.

Embedded hosts care most about *not* having their root logger mutated.
``logging.basicConfig`` on the host's root would re-route every other
library's log output, and a stray ``logging.getLogger().setLevel(...)``
would flip every logger that hadn't pinned its own level. This test
captures the root-logger snapshot before and after:

1. ``import agentao``
2. ``from agentao import Agentao``  (lazy resolution path)
3. ``Agentao(...)``                 (construction with injected LLMClient)

and asserts the snapshot is unchanged. The construction step uses an
injected ``LLMClient`` (the embed-host pattern) so the package-internal
``agentao`` logger setup that runs only on the default-LLMClient path
does not interfere with the assertion.

The check is intentionally narrow — it covers root only, not the
``agentao.*`` family. The package may freely configure its own
namespace; the contract is "stay on your side of the fence."
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch


def _root_snapshot() -> dict[str, Any]:
    root = logging.getLogger()
    return {
        "level": root.level,
        "handler_ids": [id(h) for h in root.handlers],
        "filter_ids": [id(f) for f in root.filters],
        "propagate": root.propagate,
        "disabled": root.disabled,
    }


def test_import_agentao_does_not_touch_root_logger() -> None:
    """Plain ``import agentao`` must not configure root.

    Lazy ``__getattr__`` keeps this fast (P0.5); we also assert here that
    nothing in the package-init path quietly calls ``logging.basicConfig``
    or attaches a handler to root.
    """
    before = _root_snapshot()
    import agentao  # noqa: F401

    after = _root_snapshot()
    assert before == after, (
        "import agentao mutated the root logger:\n"
        f"  before: {before}\n  after : {after}"
    )


def test_from_agentao_import_agentao_does_not_touch_root_logger() -> None:
    """Resolving the lazy ``Agentao`` attribute must not touch root either."""
    before = _root_snapshot()
    from agentao import Agentao  # noqa: F401

    after = _root_snapshot()
    assert before == after, (
        "Resolving Agentao mutated the root logger:\n"
        f"  before: {before}\n  after : {after}"
    )


def test_agentao_construction_does_not_touch_root_logger(tmp_path: Path) -> None:
    """Constructing ``Agentao(...)`` against an injected LLMClient leaves root alone.

    This is the property hosts care about most. Even when the default
    ``LLMClient`` adds a *package-namespace* file handler, root must not
    gain handlers, change level, or get a filter.
    """
    before = _root_snapshot()

    # Construct with a mock LLMClient via the documented embedded-host
    # path ("inject your own llm_client"). Going through this seam is
    # what the test claims to cover — the alternative env-backfill
    # codepath in conftest.py would not exercise the same ctor branch.
    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.model = "gpt-test"
    with patch("agentao.tooling.mcp_tools.McpClientManager"), patch(
        "agentao.tooling.mcp_tools.load_mcp_config", return_value={}
    ):
        from agentao.agent import Agentao

        agent = Agentao(working_directory=tmp_path, llm_client=mock_llm)
        try:
            after = _root_snapshot()
        finally:
            agent.close()

    assert before == after, (
        "Constructing Agentao mutated the root logger — embedded hosts "
        "rely on this not happening:\n"
        f"  before: {before}\n  after : {after}"
    )


def test_agentao_logger_namespace_is_self_contained(tmp_path: Path) -> None:
    """Any handler agentao adds must live under the ``agentao`` namespace.

    Hosts that filter on logger name expect agentao output to be tagged
    consistently. A stray handler on ``""`` (root) or on a sibling
    namespace would defeat that filter.
    """
    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.model = "gpt-test"
    with patch("agentao.tooling.mcp_tools.McpClientManager"), patch(
        "agentao.tooling.mcp_tools.load_mcp_config", return_value={}
    ):
        from agentao.agent import Agentao

        agent = Agentao(working_directory=tmp_path, llm_client=mock_llm)
        try:
            # Iterate every existing logger; any handler bound to a name
            # outside the ``agentao`` family violates the contract.
            offenders: list[str] = []
            manager = logging.Logger.manager
            for name, logger in list(manager.loggerDict.items()):
                if not isinstance(logger, logging.Logger):
                    continue
                if logger.handlers and not (
                    name == "agentao" or name.startswith("agentao.")
                ):
                    # Some test harnesses pre-populate other loggers; only
                    # complain about handlers tagged as ours.
                    bad = [
                        h
                        for h in logger.handlers
                        if getattr(h, "_agentao_llm_file_handler", False)
                    ]
                    if bad:
                        offenders.append(f"{name}: {bad!r}")
            assert not offenders, (
                "agentao installed handlers outside its own namespace:\n  "
                + "\n  ".join(offenders)
            )
        finally:
            agent.close()
