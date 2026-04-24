"""System-prompt construction for Agentao.

Extracted from ``agentao/agent.py`` so the section text and the
composition logic can evolve without touching the agent core. The
``Agentao`` class keeps thin wrapper methods that delegate here, so
existing tests and any external callers continue to work.
"""

from .builder import SystemPromptBuilder
from .sections import (
    build_identity_section,
    build_reliability_section,
    build_task_classification_section,
    build_execution_protocol_section,
    build_completion_standard_section,
    build_untrusted_input_section,
    build_operational_guidelines,
)

__all__ = [
    "SystemPromptBuilder",
    "build_identity_section",
    "build_reliability_section",
    "build_task_classification_section",
    "build_execution_protocol_section",
    "build_completion_standard_section",
    "build_untrusted_input_section",
    "build_operational_guidelines",
]
