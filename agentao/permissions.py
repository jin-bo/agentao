"""Declarative permission rule engine for tool execution control."""

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionMode(Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "full-access"
    PLAN = "plan"  # Internal: read-only writes, safe shell commands allowed


# Preset rule lists for each mode. Evaluated after project/user JSON rules.
_PRESET_RULES: Dict[str, List[Dict[str, Any]]] = {
    "read-only": [],  # ToolRunner handles this via is_read_only check; no extra rules needed
    "workspace-write": [
        {"tool": "write_file", "action": "allow"},
        {"tool": "replace", "action": "allow"},
        {
            "tool": "run_shell_command",
            "args": {
                # Allowlist of genuinely read-only shell commands.
                # Rules:
                #  - No shell operators (&&, ||, ;, |, $(...), backticks,
                #    redirects, newlines) so command smuggling is impossible.
                #  - git: only subcommands that cannot mutate state. Excluded:
                #    branch/tag/remote (accept -D/-d/add flags), push, reset,
                #    clean, checkout. Allowed: status, log, diff, show,
                #    stash list, shortlog, describe, blame, ls-files, ls-tree,
                #    rev-parse, config --get*.
                #  - find excluded (find . -delete is destructive).
                #  - ls, cat, echo, pwd, which, file, head, tail, wc, diff,
                #    grep, du, df, ps, env are safe read-only metadata commands.
                # Use \b (word boundary) so bare commands like `ls` or `env`
                # match in addition to commands with arguments like `ls -la`.
                "command": (
                    r"^("
                    r"git (status|log|diff|show|stash list"
                    r"|shortlog|describe|blame|ls-files|ls-tree|rev-parse|config --get)"
                    r"|ls\b|cat\b|echo\b|pwd\b|which\b|file\b|head\b|tail\b"
                    r"|wc\b|diff\b|grep\b|du\b|df\b|ps\b|env\b"
                    r")"
                    r"(?:[^;&|`$<>\n\r])*$"
                )
            },
            "action": "allow",
        },
        {
            "tool": "run_shell_command",
            "args": {"command": r"rm\s+-rf|sudo\s|mkfs|dd\s+if="},
            "action": "deny",
        },
        {"tool": "run_shell_command", "action": "ask"},
        {"tool": "web_fetch", "action": "ask"},
        {"tool": "google_web_search", "action": "ask"},
    ],
    "full-access": [
        {"tool": "*", "action": "allow"},
    ],
    # Plan mode: allows safe read-only shell commands (diff, git diff, ls, cat, grep, …)
    # but denies all file-write and session-mutation operations. Use this instead of
    # "read-only" so that the ToolRunner does not short-circuit via is_read_only and
    # shell analysis can still run.
    "plan": [
        {"tool": "write_file", "action": "deny"},
        {"tool": "replace", "action": "deny"},
        # Deny memory writes and task mutations — plan mode is research-only.
        {"tool": "save_memory", "action": "deny"},
        {"tool": "delete_memory", "action": "deny"},
        {"tool": "clear_all_memories", "action": "deny"},
        {"tool": "todo_write", "action": "deny"},
        {
            "tool": "run_shell_command",
            "args": {
                "command": (
                    r"^("
                    r"git (status|log|diff|show|stash list"
                    r"|shortlog|describe|blame|ls-files|ls-tree|rev-parse|config --get)"
                    r"|ls\b|cat\b|echo\b|pwd\b|which\b|file\b|head\b|tail\b"
                    r"|wc\b|diff\b|grep\b|du\b|df\b|ps\b|env\b"
                    r")"
                    r"(?:[^;&|`$<>\n\r])*$"
                )
            },
            "action": "allow",
        },
        {"tool": "run_shell_command", "args": {"command": r"rm\s+-rf|sudo\s|mkfs|dd\s+if="}, "action": "deny"},
        {"tool": "run_shell_command", "action": "deny"},
        {"tool": "web_fetch", "action": "ask"},
        {"tool": "google_web_search", "action": "ask"},
    ],
}


class PermissionEngine:
    """Evaluates permission rules to decide tool execution policy.

    Rules are loaded from (higher priority listed first):
    - .agentao/permissions.json  (project-level)
    - ~/.agentao/permissions.json (user-level)

    Rule format::

        {
            "rules": [
                {"tool": "run_shell_command", "args": {"command": "^git "}, "action": "allow"},
                {"tool": "write_file", "action": "ask"},
                {"tool": "run_shell_command", "args": {"command": "rm -rf"}, "action": "deny"}
            ]
        }

    When no rule matches a tool call, ``decide()`` returns ``None`` and the
    caller falls back to the tool's own ``requires_confirmation`` attribute.
    """

    def __init__(self):
        self.rules: List[Dict[str, Any]] = []
        self._mode_rules: List[Dict[str, Any]] = []
        self.active_mode: PermissionMode = PermissionMode.WORKSPACE_WRITE
        self._load_rules()
        self._mode_rules = _PRESET_RULES[self.active_mode.value]

    def set_mode(self, mode: PermissionMode) -> None:
        """Switch the active permission preset. Mode rules are evaluated after project/user rules."""
        self.active_mode = mode
        self._mode_rules = _PRESET_RULES[mode.value]

    def _load_rules(self):
        """Load rules from user then project config files (project takes priority)."""
        user_rules = self._load_file(Path.home() / ".agentao" / "permissions.json")
        project_rules = self._load_file(Path.cwd() / ".agentao" / "permissions.json")
        # Project rules prepended so they are evaluated first
        self.rules = project_rules + user_rules

    def _load_file(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("rules", [])
        except (IOError, json.JSONDecodeError):
            return []

    def decide(self, tool_name: str, tool_args: Dict[str, Any]) -> Optional[PermissionDecision]:
        """Evaluate rules for a tool call.

        Evaluation order (first match wins):
          - full-access / plan mode: mode preset rules run first (can't be overridden)
          - all other modes: project JSON → user JSON → mode preset rules

        Returns:
            PermissionDecision.ALLOW / DENY / ASK for the first matching rule,
            or None if no rule matches.
        """
        if self.active_mode in (PermissionMode.FULL_ACCESS, PermissionMode.PLAN):
            rule_order = self._mode_rules + self.rules
        else:
            rule_order = self.rules + self._mode_rules
        for rule in rule_order:
            if self._matches(rule, tool_name, tool_args):
                action = rule.get("action", "ask").lower()
                if action == "allow":
                    return PermissionDecision.ALLOW
                elif action == "deny":
                    return PermissionDecision.DENY
                else:
                    return PermissionDecision.ASK
        return None

    def _matches(self, rule: Dict[str, Any], tool_name: str, tool_args: Dict[str, Any]) -> bool:
        rule_tool = rule.get("tool", "*")
        if rule_tool != "*" and not self._match_pattern(rule_tool, tool_name):
            return False
        for arg_key, arg_pattern in rule.get("args", {}).items():
            arg_value = str(tool_args.get(arg_key, ""))
            try:
                if not re.search(arg_pattern, arg_value):
                    return False
            except re.error:
                if arg_pattern != arg_value:
                    return False
        return True

    def _match_pattern(self, pattern: str, value: str) -> bool:
        try:
            return bool(re.fullmatch(pattern, value))
        except re.error:
            return pattern == value

    def get_rules_display(self) -> str:
        """Return a human-readable summary of loaded rules and active mode."""
        symbols = {"allow": "✓ ALLOW", "deny": "✗ DENY", "ask": "? ASK"}
        lines = [f"Permission Mode: {self.active_mode.value}"]
        lines.append(f"Preset rules: {len(self._mode_rules)} | Custom rules: {len(self.rules)}\n")

        if self.rules:
            order_note = "evaluated after mode preset" if self.active_mode in (PermissionMode.FULL_ACCESS, PermissionMode.PLAN) else "evaluated before presets"
            lines.append(f"Custom Rules ({len(self.rules)} total, {order_note}):\n")
            for i, rule in enumerate(self.rules, 1):
                tool = rule.get("tool", "*")
                action = rule.get("action", "ask").lower()
                args = rule.get("args", {})
                label = symbols.get(action, f"? {action.upper()}")
                line = f"  {i}. [{label}] {tool}"
                if args:
                    for k, v in args.items():
                        line += f"\n        {k}: {v}"
                lines.append(line)
        else:
            lines.append(
                "No custom rules. Create .agentao/permissions.json to add rules.\n\n"
                "Example:\n"
                '  {"rules": [\n'
                '    {"tool": "run_shell_command", "args": {"command": "^git "}, "action": "allow"},\n'
                '    {"tool": "write_file", "action": "ask"},\n'
                '    {"tool": "*", "action": "ask"}\n'
                "  ]}"
            )
        return "\n".join(lines)
