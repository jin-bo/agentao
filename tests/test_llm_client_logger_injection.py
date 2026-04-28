"""Tests for ``LLMClient`` logger ownership inversion (Issue #8).

Embedded hosts that bring their own logging stack must be able to
construct an :class:`LLMClient` without having the ``agentao`` package
root logger mutated underneath them. The contract:

- ``LLMClient(logger=injected)`` → ``self.logger`` is the injected one;
  ``logging.getLogger("agentao")`` level and handler list are untouched.
- ``LLMClient(log_file=None)`` → no file handler is attached, regardless
  of logger injection.
- ``LLMClient()`` (default) → existing CLI behavior is preserved:
  package root level set to DEBUG and a marker-tagged file handler
  attached.
"""

from __future__ import annotations

import logging
import sys
from typing import Iterable
from unittest.mock import MagicMock

import pytest

from agentao.llm.client import LLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_pkg_logger():
    """Snapshot and restore the ``agentao`` package logger between tests.

    Without this, a test that mutates the package root would leak state
    into subsequent tests in the same process.
    """
    pkg = logging.getLogger("agentao")
    saved_level = pkg.level
    saved_handlers = list(pkg.handlers)
    pkg.handlers = []
    pkg.setLevel(logging.NOTSET)
    yield pkg
    for h in pkg.handlers:
        try:
            h.close()
        except Exception:
            pass
    pkg.handlers = saved_handlers
    pkg.setLevel(saved_level)


def _marker_handlers(pkg: logging.Logger) -> Iterable[logging.Handler]:
    return [h for h in pkg.handlers if getattr(h, "_agentao_llm_file_handler", False)]


# ---------------------------------------------------------------------------
# Logger injection skips package-root mutation
# ---------------------------------------------------------------------------


def test_injected_logger_is_used_as_self_logger(_reset_pkg_logger):
    injected = logging.getLogger("host.app.embedded")
    client = LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        logger=injected,
    )
    assert client.logger is injected


def test_injected_logger_does_not_set_package_root_level(_reset_pkg_logger):
    pkg = _reset_pkg_logger
    pkg_level_before = pkg.level

    LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        logger=MagicMock(spec=logging.Logger),
    )

    assert pkg.level == pkg_level_before, (
        "package root level must remain untouched when logger is injected"
    )


def test_injected_logger_does_not_attach_handlers_to_package_root(
    _reset_pkg_logger, tmp_path
):
    pkg = _reset_pkg_logger
    handlers_before = list(pkg.handlers)

    LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        log_file=str(tmp_path / "host_owned.log"),
        logger=MagicMock(spec=logging.Logger),
    )

    assert pkg.handlers == handlers_before, (
        "package root handler list must remain untouched when logger is injected"
    )


def test_injected_logger_preserves_outsider_handlers(_reset_pkg_logger):
    """A host's own stderr handler must survive LLMClient construction."""
    pkg = _reset_pkg_logger
    external = logging.StreamHandler(sys.stderr)
    pkg.addHandler(external)
    handlers_before = list(pkg.handlers)

    LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        logger=MagicMock(spec=logging.Logger),
    )

    assert pkg.handlers == handlers_before
    assert external in pkg.handlers


def test_repeated_injected_construction_is_idempotent_against_root(
    _reset_pkg_logger,
):
    """Reconstructing with logger= must remain a no-op against the root.

    Repeated model swaps in ACP mode rebuild the LLMClient every time;
    embedded hosts cannot afford a slow leak of handler state.
    """
    pkg = _reset_pkg_logger
    handlers_before = list(pkg.handlers)
    level_before = pkg.level

    for _ in range(5):
        LLMClient(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            model="gpt-test",
            logger=MagicMock(spec=logging.Logger),
        )

    assert pkg.handlers == handlers_before
    assert pkg.level == level_before


# ---------------------------------------------------------------------------
# log_file=None → no file handler
# ---------------------------------------------------------------------------


def test_log_file_none_does_not_attach_file_handler(_reset_pkg_logger):
    pkg = _reset_pkg_logger
    LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        log_file=None,
    )
    assert list(_marker_handlers(pkg)) == [], (
        "log_file=None must skip the file handler"
    )


def test_log_file_none_with_injected_logger_is_zero_side_effect(_reset_pkg_logger):
    """Acceptance criterion: ``LLMClient(log_file=None, logger=mock)``
    is a clean construction with no package-root side effects."""
    pkg = _reset_pkg_logger
    handlers_before = list(pkg.handlers)
    level_before = pkg.level

    LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        log_file=None,
        logger=MagicMock(spec=logging.Logger),
    )

    assert pkg.handlers == handlers_before
    assert pkg.level == level_before


# ---------------------------------------------------------------------------
# Default behavior (no logger, default log_file) is preserved
# ---------------------------------------------------------------------------


def test_default_construction_takes_ownership_of_package_root(
    _reset_pkg_logger, tmp_path
):
    pkg = _reset_pkg_logger
    LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        log_file=str(tmp_path / "agentao.log"),
    )

    # Existing CLI invariant: package-root level is DEBUG and exactly
    # one marker-tagged file handler was attached.
    assert pkg.level == logging.DEBUG
    assert len(list(_marker_handlers(pkg))) == 1
