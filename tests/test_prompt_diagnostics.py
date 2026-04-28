"""Tests for SystemPromptBuilder per-section token diagnostics.

Every ``build()`` emits a ``prompt_sections`` log line with per-section
token counts so we can see how each section grows over time.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import Mock, patch

import pytest

from agentao.prompts.builder import SystemPromptBuilder


def _make_agent(**kwargs):
    """Construct a minimally-mocked Agentao for prompt-builder tests.

    Mirrors the helper in tests/test_system_prompt_sections.py — keeps
    the LLMClient stubbed so we don't need real credentials.
    """
    from pathlib import Path
    kwargs.setdefault("working_directory", Path.cwd())
    with patch("agentao.agent.LLMClient") as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"
        from agentao.agent import Agentao
        return Agentao(**kwargs)


# ---------------------------------------------------------------------------
# Section dict structure
# ---------------------------------------------------------------------------


def test_build_sections_contains_stable_prefix_keys():
    agent = _make_agent()
    sections = SystemPromptBuilder(agent)._build_sections()

    expected_stable = {
        "identity",
        "reliability",
        "task_classification",
        "execution_protocol",
        "completion_standard",
        "untrusted_input",
        "operational_guidelines",
    }
    assert expected_stable.issubset(sections.keys())


def test_build_sections_preserves_documented_order():
    """Insertion order is the assembly order documented in the builder."""
    agent = _make_agent()
    sections = SystemPromptBuilder(agent)._build_sections()
    keys = list(sections.keys())

    # Stable-prefix sections (mandatory) must come in this order.
    stable_order = [
        "identity",
        "reliability",
        "task_classification",
        "execution_protocol",
        "completion_standard",
        "untrusted_input",
        "operational_guidelines",
    ]
    indices = [keys.index(k) for k in stable_order]
    assert indices == sorted(indices), (
        f"Stable prefix out of order: {keys}"
    )


def test_build_returns_concatenation_of_section_values():
    """``build()`` must return exactly the concatenation of section values
    so the section breakdown is faithful — no hidden glue, no missing
    content."""
    agent = _make_agent()
    builder = SystemPromptBuilder(agent)
    sections = builder._build_sections()
    expected = "".join(sections.values())
    # Build again — _build_sections() and build() are independent calls;
    # they must produce byte-identical content given identical state.
    assert builder.build() == expected


def test_empty_optional_sections_are_omitted():
    """An agent with no project_instructions / no plan / no todos must
    not have those keys in the section dict.

    We explicitly clear ``project_instructions`` because the test cwd
    may carry a real AGENTAO.md (it does in this repo)."""
    agent = _make_agent()
    agent.project_instructions = None
    sections = SystemPromptBuilder(agent)._build_sections()
    assert "project_instructions" not in sections
    # plan_prompt only exists in plan mode
    assert "plan_prompt" not in sections
    # todos only exists if there are todos
    assert "todos" not in sections


def test_project_instructions_section_appears_when_set():
    agent = _make_agent()
    agent.project_instructions = "Test project rules."
    sections = SystemPromptBuilder(agent)._build_sections()
    assert "project_instructions" in sections
    assert "Test project rules." in sections["project_instructions"]


# ---------------------------------------------------------------------------
# Diagnostic log emission
# ---------------------------------------------------------------------------


def test_build_emits_prompt_sections_log_line(caplog):
    agent = _make_agent()
    with caplog.at_level(logging.INFO, logger="agentao.prompt_diag"):
        SystemPromptBuilder(agent).build()

    diag_records = [r for r in caplog.records if r.name == "agentao.prompt_diag"]
    assert len(diag_records) == 1
    msg = diag_records[0].getMessage()
    assert "prompt_sections" in msg
    assert "total_tokens=" in msg
    assert "breakdown=" in msg


def test_log_breakdown_matches_section_keys(caplog):
    agent = _make_agent()
    with caplog.at_level(logging.INFO, logger="agentao.prompt_diag"):
        builder = SystemPromptBuilder(agent)
        sections = builder._build_sections()
        builder.build()

    record = next(r for r in caplog.records if r.name == "agentao.prompt_diag")
    msg = record.getMessage()
    # Parse the JSON breakdown out of the log line.
    breakdown_json = msg.split("breakdown=", 1)[1]
    breakdown = json.loads(breakdown_json)
    assert set(breakdown.keys()) == set(sections.keys())
    # Token counts are non-negative ints.
    for name, count in breakdown.items():
        assert isinstance(count, int)
        assert count >= 0, f"section {name} has negative token count {count}"


def test_log_total_matches_sum_of_breakdown(caplog):
    agent = _make_agent()
    with caplog.at_level(logging.INFO, logger="agentao.prompt_diag"):
        SystemPromptBuilder(agent).build()

    record = next(r for r in caplog.records if r.name == "agentao.prompt_diag")
    msg = record.getMessage()
    total = int(msg.split("total_tokens=", 1)[1].split(" ", 1)[0])
    breakdown = json.loads(msg.split("breakdown=", 1)[1])
    assert total == sum(breakdown.values())


def test_diagnostics_swallow_estimator_failure(caplog, monkeypatch):
    """If the token estimator raises, build() must still return a prompt."""
    agent = _make_agent()

    def _boom(text):
        raise RuntimeError("simulated tiktoken failure")

    monkeypatch.setattr(
        agent.context_manager, "count_tokens_in_text", _boom
    )
    with caplog.at_level(logging.INFO, logger="agentao.prompt_diag"):
        prompt = SystemPromptBuilder(agent).build()

    assert prompt  # build still produces output
    # No diagnostic line emitted (the try/except swallowed it).
    diag = [r for r in caplog.records if r.name == "agentao.prompt_diag"]
    assert diag == []


def test_section_token_counts_are_cached_across_builds(monkeypatch, caplog):
    """Stable-prefix sections are byte-identical across turns. The
    diagnostic must memoize per-section counts keyed on text so the
    second build only re-tokenizes sections whose text changed."""
    agent = _make_agent()
    calls = []
    real_count = agent.context_manager.count_tokens_in_text

    def counting_proxy(text):
        calls.append(text)
        return real_count(text)

    monkeypatch.setattr(
        agent.context_manager, "count_tokens_in_text", counting_proxy
    )

    builder = SystemPromptBuilder(agent)
    with caplog.at_level(logging.INFO, logger="agentao.prompt_diag"):
        builder.build()
        first_call_count = len(calls)
        assert first_call_count > 0  # sanity

        builder.build()
        second_call_count = len(calls) - first_call_count

    # Second build must re-tokenize *fewer* sections than the first
    # (everything stable hits the cache; only volatile sections that
    # actually changed are recounted).
    assert second_call_count < first_call_count


def test_diagnostics_handle_missing_context_manager(caplog):
    """If agent has no context_manager (degenerate test scenarios), the
    diagnostic just no-ops — no crash, no log line."""
    agent = _make_agent()
    # Drop the attribute entirely
    del agent.context_manager
    with caplog.at_level(logging.INFO, logger="agentao.prompt_diag"):
        prompt = SystemPromptBuilder(agent).build()

    assert prompt
    diag = [r for r in caplog.records if r.name == "agentao.prompt_diag"]
    assert diag == []
