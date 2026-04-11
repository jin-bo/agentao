"""Plugin system data models and diagnostics types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class PluginWarningSeverity(str, Enum):
    WARNING = "warning"
    INFO = "info"


@dataclass
class PluginWarning:
    """Non-fatal issue encountered during plugin loading."""

    plugin_name: str
    message: str
    severity: PluginWarningSeverity = PluginWarningSeverity.WARNING
    field: str | None = None

    def __str__(self) -> str:
        prefix = f"[{self.plugin_name}]"
        if self.field:
            prefix += f" ({self.field})"
        return f"{prefix} {self.message}"


@dataclass
class PluginLoadError:
    """Fatal issue that prevents a plugin from loading."""

    plugin_name: str
    message: str
    exception: Exception | None = None

    def __str__(self) -> str:
        s = f"[{self.plugin_name}] {self.message}"
        if self.exception:
            s += f": {self.exception}"
        return s


# ---------------------------------------------------------------------------
# Manifest sub-types
# ---------------------------------------------------------------------------

@dataclass
class PluginAuthor:
    name: str
    email: str | None = None
    url: str | None = None


@dataclass
class PluginDependencyRef:
    plugin_name: str
    version: str | None = None
    marketplace: str | None = None


@dataclass
class PluginCommandMetadata:
    source: str | None = None
    content: str | None = None
    description: str | None = None
    argument_hint: str | None = None
    model: str | None = None
    allowed_tools: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

@dataclass
class PluginManifest:
    """Parsed representation of a plugin.json file."""

    name: str
    version: str | None = None
    description: str | None = None
    author: PluginAuthor | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: list[str] = field(default_factory=list)
    dependencies: list[PluginDependencyRef] = field(default_factory=list)

    # Component references — raw values from plugin.json.
    # Actual resolution happens in the loader.
    commands: str | list[str] | dict[str, PluginCommandMetadata] | None = None
    skills: str | list[str] | None = None
    agents: str | list[str] | None = None
    hooks: str | dict[str, Any] | list[str | dict[str, Any]] | None = None
    mcp_servers: str | dict[str, Any] | None = None

    # Fields present in the JSON but not supported by Agentao.
    unsupported_fields: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline types
# ---------------------------------------------------------------------------

@dataclass
class PluginCandidate:
    """A plugin directory that passed manifest parsing but has not yet gone
    through precedence resolution."""

    name: str
    root_path: Path
    source: Literal["global", "project", "inline"]
    source_rank: int  # global=0, project=1, inline=2
    manifest: PluginManifest
    marketplace: str | None = None       # "openai-codex" / "local" / None (inline)
    qualified_name: str | None = None    # "name@marketplace" or None
    warnings: list[PluginWarning] = field(default_factory=list)


@dataclass
class LoadedPlugin:
    """A fully resolved plugin ready for runtime integration."""

    name: str
    version: str | None
    root_path: Path
    source: Literal["global", "project", "inline"]
    manifest: PluginManifest
    marketplace: str | None = None       # "openai-codex" / "local" / None (inline)
    qualified_name: str | None = None    # "name@marketplace" or None

    # Resolved component paths (populated by the loader).
    skill_roots: list[Path] = field(default_factory=list)
    command_paths: list[Path] = field(default_factory=list)
    agent_paths: list[Path] = field(default_factory=list)
    hook_specs: list[Any] = field(default_factory=list)
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)

    warnings: list[PluginWarning] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Skill / command runtime entries
# ---------------------------------------------------------------------------

@dataclass
class PluginSkillEntry:
    """A skill-like prompt entry produced by a plugin.

    These are registered into SkillManager as available skills alongside
    built-in and project skills.
    """

    runtime_name: str  # e.g. "plugin_name:skill_name"
    plugin_name: str
    source_kind: Literal["plugin-skill", "plugin-command"]
    source_path: Path | None = None
    content: str | None = None
    description: str | None = None
    argument_hint: str | None = None
    model: str | None = None
    allowed_tools: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent runtime entries
# ---------------------------------------------------------------------------

@dataclass
class PluginAgentDefinition:
    """An agent definition produced by a plugin.

    These are registered into AgentManager alongside built-in and
    project-level agent definitions.
    """

    runtime_name: str  # e.g. "plugin_name:agent_name"
    plugin_name: str
    source_path: Path
    raw_markdown: str
    description: str | None = None


# ---------------------------------------------------------------------------
# Hook types
# ---------------------------------------------------------------------------

# Supported hook events.
SUPPORTED_HOOK_EVENTS: set[str] = {
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
}

# Hook types supported at runtime.
SUPPORTED_HOOK_TYPES: set[str] = {"command", "prompt"}

# Hook types recognised but not yet runnable.
KNOWN_UNSUPPORTED_HOOK_TYPES: set[str] = {"http", "agent"}


@dataclass
class ParsedHookRule:
    """A single hook rule from a hooks.json file."""

    event: str
    hook_type: str  # "command" | "prompt" | "http" | "agent"
    command: str | None = None
    prompt: str | None = None
    timeout: int = 60
    matcher: dict[str, Any] | None = None
    plugin_name: str | None = None

    @property
    def is_supported(self) -> bool:
        return self.hook_type in SUPPORTED_HOOK_TYPES and self.event in SUPPORTED_HOOK_EVENTS


@dataclass
class HookAttachmentRecord:
    """An attachment produced by running a hook."""

    attachment_type: str  # hook_additional_context | hook_success | hook_stopped_continuation | hook_blocking_error
    payload: dict[str, Any]
    hook_name: str
    hook_event: str
    tool_use_id: str = ""
    uuid: str = ""
    timestamp: str = ""


@dataclass
class PreparedTurnMessage:
    """A message to inject into the conversation turn."""

    role: Literal["user", "assistant", "system", "tool"]
    content: str
    is_meta: bool = False
    source: str | None = None


@dataclass
class PreparedUserTurn:
    """Result of prepare_user_turn() — ready for agent.chat()."""

    original_user_message: str
    hook_attachments: list[HookAttachmentRecord] = field(default_factory=list)
    normalized_messages: list[PreparedTurnMessage] = field(default_factory=list)
    should_query: bool = True
    stop_reason: str | None = None


@dataclass
class UserPromptSubmitResult:
    """Aggregated result of all hooks for a single UserPromptSubmit event."""

    blocking_error: str | None = None
    prevent_continuation: bool = False
    stop_reason: str | None = None
    additional_contexts: list[str] = field(default_factory=list)
    messages: list[HookAttachmentRecord] = field(default_factory=list)
