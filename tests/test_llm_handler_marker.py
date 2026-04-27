"""Tests for P2 of ACP_STDIO_AUTH_FIX_PLAN.

The fix replaces ``pkg_logger.handlers.clear()`` with marker-based
selective cleanup. Two invariants:

1. Handlers we don't own (e.g. the stderr StreamHandler installed by
   ``AcpServer._install_log_guard``) are preserved across LLMClient
   construction.
2. LLMClient-owned RotatingFileHandlers do not pile up across repeated
   construction — only one carries the marker at any time.
"""

from __future__ import annotations

import logging
import sys

import pytest

from agentao.llm.client import LLMClient


@pytest.fixture(autouse=True)
def _reset_pkg_logger():
    pkg = logging.getLogger("agentao")
    saved = list(pkg.handlers)
    pkg.handlers = []
    yield pkg
    for h in pkg.handlers:
        try:
            h.close()
        except Exception:
            pass
    pkg.handlers = saved


def _marker_handlers(pkg):
    return [h for h in pkg.handlers if getattr(h, "_agentao_llm_file_handler", False)]


def test_external_stderr_handler_preserved(_reset_pkg_logger, tmp_path):
    pkg = _reset_pkg_logger
    # Mimic what AcpServer._install_log_guard does before LLMClient lands.
    external = logging.StreamHandler(sys.stderr)
    external.setLevel(logging.INFO)
    pkg.addHandler(external)

    LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        log_file=str(tmp_path / "agentao.log"),
    )

    assert external in pkg.handlers, (
        "external stderr StreamHandler must survive LLMClient construction"
    )


def test_repeated_construction_yields_single_file_handler(_reset_pkg_logger, tmp_path):
    pkg = _reset_pkg_logger
    log_path = str(tmp_path / "agentao.log")

    for _ in range(5):
        LLMClient(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            model="gpt-test",
            log_file=log_path,
        )

    marked = _marker_handlers(pkg)
    assert len(marked) == 1, (
        f"expected exactly one LLMClient-owned file handler, found {len(marked)}"
    )


def test_external_handler_count_unchanged_across_reconstruction(
    _reset_pkg_logger, tmp_path
):
    pkg = _reset_pkg_logger
    external_a = logging.StreamHandler(sys.stderr)
    external_b = logging.StreamHandler(sys.stderr)
    pkg.addHandler(external_a)
    pkg.addHandler(external_b)
    pre_external_count = sum(
        1 for h in pkg.handlers
        if h in (external_a, external_b)
    )

    log_path = str(tmp_path / "agentao.log")
    for _ in range(3):
        LLMClient(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            model="gpt-test",
            log_file=log_path,
        )

    post_external_count = sum(
        1 for h in pkg.handlers
        if h in (external_a, external_b)
    )
    assert post_external_count == pre_external_count, (
        "external handlers should be preserved 1:1"
    )

    marked = _marker_handlers(pkg)
    assert len(marked) == 1


def test_log_file_writes_only_once_per_message(_reset_pkg_logger, tmp_path):
    """Constructing LLMClient N times must not cause N-fold duplicate
    lines in the log file (the regression that an unguarded fix would
    introduce)."""
    log_path = tmp_path / "agentao.log"
    for _ in range(4):
        LLMClient(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            model="gpt-test",
            log_file=str(log_path),
        )

    # Each LLMClient init logs "LLMClient initialized with model: gpt-test".
    # Four constructions → exactly four such lines, not 1+2+3+4=10.
    contents = log_path.read_text(encoding="utf-8")
    occurrences = contents.count("LLMClient initialized with model: gpt-test")
    assert occurrences == 4, (
        f"expected 4 init log lines, got {occurrences} — duplicate handlers regression?"
    )
