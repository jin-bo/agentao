"""Declarative permission rule engine for tool execution control."""

import copy
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .harness.models import ActivePermissions


class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionDecisionDetail:
    """Structured outcome of one permission evaluation.

    Carries enough information for the runtime to build a public
    :class:`PermissionDecisionEvent` without coupling the
    :class:`PermissionEngine` directly to event delivery. ``decision``
    is the existing enum; ``matched_rule`` is a JSON-safe shallow copy
    of the rule that matched (or ``None`` for the no-rule fallback);
    ``reason`` is a stable, redactable string the projection layer can
    surface to hosts.
    """

    __slots__ = ("decision", "matched_rule", "reason")

    def __init__(
        self,
        decision: PermissionDecision,
        matched_rule: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        self.decision = decision
        self.matched_rule = matched_rule
        self.reason = reason


class PermissionMode(Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "full-access"
    PLAN = "plan"  # Internal: read-only writes, safe shell commands allowed


def _extract_domain(url: str) -> Optional[str]:
    """Extract and normalize the hostname from a URL for domain matching.

    Returns lowercase hostname (no port), or None if parsing fails.
    Handles missing scheme by prepending https://.
    """
    if not url:
        return None
    # urlparse needs a scheme to correctly identify the hostname
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname  # lowercase, no port, no userinfo
        return hostname if hostname else None
    except Exception:
        return None


def _domain_matches(hostname: str, patterns: List[str]) -> bool:
    """Check if hostname matches any pattern in the list.

    Pattern semantics:
    - Leading dot (e.g. ".github.com"): suffix match — matches
      "github.com" and "api.github.com" but not "notgithub.com".
    - No leading dot (e.g. "r.jina.ai"): exact match only.
    """
    for pattern in patterns:
        pattern_lower = pattern.lower()
        if pattern_lower.startswith("."):
            # Suffix match: ".github.com" matches "github.com" and "x.github.com"
            bare = pattern_lower[1:]  # "github.com"
            if hostname == bare or hostname.endswith(pattern_lower):
                return True
        else:
            # Exact match
            if hostname == pattern_lower:
                return True
    return False


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
        # Domain-tiered web_fetch: allowlist auto-allows, blocklist auto-denies, rest asks
        {
            "tool": "web_fetch",
            "domain": {"allowlist": [".github.com", ".docs.python.org", ".wikipedia.org", "r.jina.ai", ".pypi.org", ".readthedocs.io"]},
            "action": "allow",
        },
        {
            "tool": "web_fetch",
            "domain": {"blocklist": ["localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", ".internal", ".local", "::1"]},
            "action": "deny",
        },
        {"tool": "web_fetch", "action": "ask"},
        {"tool": "web_search", "action": "ask"},
    ],
    "full-access": [
        {"tool": "*", "action": "allow"},
    ],
    # Plan mode: allows safe read-only shell commands (diff, git diff, ls, cat, grep, …)
    # but denies all file-write and session-mutation operations. Use this instead of
    # "read-only" so that the ToolRunner does not short-circuit via is_read_only and
    # shell analysis can still run.
    "plan": [
        {"tool": "plan_save", "action": "allow"},
        {"tool": "plan_finalize", "action": "allow"},
        {"tool": "write_file", "action": "deny"},
        {"tool": "replace", "action": "deny"},
        # Deny memory writes and task mutations — plan mode is research-only.
        {"tool": "save_memory", "action": "deny"},
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
        # Domain-tiered web_fetch (same as workspace-write)
        {
            "tool": "web_fetch",
            "domain": {"allowlist": [".github.com", ".docs.python.org", ".wikipedia.org", "r.jina.ai", ".pypi.org", ".readthedocs.io"]},
            "action": "allow",
        },
        {
            "tool": "web_fetch",
            "domain": {"blocklist": ["localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", ".internal", ".local", "::1"]},
            "action": "deny",
        },
        {"tool": "web_fetch", "action": "ask"},
        {"tool": "web_search", "action": "ask"},
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

    def __init__(
        self,
        *,
        project_root: Path,
        user_root: Optional[Path] = None,
    ):
        """Initialize the permission engine.

        Args:
            project_root: Project directory whose
                ``<project_root>/.agentao/permissions.json`` is loaded
                for project-level rules. Required: the engine performs
                no implicit cwd resolution.
            user_root: Optional user-scope directory whose
                ``<user_root>/permissions.json`` is loaded for
                cross-project rules. ``None`` (the default) skips the
                user-scope read; pass an explicit path (typically
                ``~/.agentao``) to opt in.
        """
        if project_root is None:
            raise TypeError(
                "PermissionEngine requires a project_root keyword argument."
            )
        self._project_root: Path = project_root
        self._user_root: Optional[Path] = user_root
        self.rules: List[Dict[str, Any]] = []
        self._mode_rules: List[Dict[str, Any]] = []
        self.active_mode: PermissionMode = PermissionMode.WORKSPACE_WRITE
        # File-source labels populated during ``_load_rules``; the preset
        # source is composed dynamically from ``active_mode`` so a mode
        # switch is reflected without re-reading disk. ``injected:*``
        # entries are appended by hosts via :meth:`add_loaded_source`.
        self._file_sources: List[str] = []
        self._injected_sources: List[str] = []
        # Cached :class:`ActivePermissions` projection. Invalidated by
        # mode switches and source-list mutations so the permission
        # decision hot path can call ``active_permissions()`` cheaply.
        self._active_cache: Optional["ActivePermissions"] = None
        self._load_rules()
        self._mode_rules = _PRESET_RULES[self.active_mode.value]

    def set_mode(self, mode: PermissionMode) -> None:
        """Switch the active permission preset. Mode rules are evaluated after project/user rules."""
        self.active_mode = mode
        self._mode_rules = _PRESET_RULES[mode.value]
        self._active_cache = None

    def add_loaded_source(self, label: str) -> None:
        """Record an injected policy source label (``injected:<name>``).

        Hosts that layer policy on top of file/preset sources call this
        so :meth:`active_permissions` reports a complete provenance
        list. Duplicate labels are coalesced.
        """
        if not isinstance(label, str) or not label:
            return
        if label not in self._injected_sources:
            self._injected_sources.append(label)
            self._active_cache = None

    def _load_rules(self):
        """Load rules from user then project config files (project takes priority)."""
        sources: List[str] = []
        if self._user_root is not None:
            user_path = self._user_root / "permissions.json"
            user_rules, user_loaded = self._load_file(user_path)
            if user_loaded:
                sources.append(f"user:{user_path}")
        else:
            user_rules = []
        project_path = self._project_root / ".agentao" / "permissions.json"
        project_rules, project_loaded = self._load_file(project_path)
        if project_loaded:
            try:
                rel = project_path.relative_to(self._project_root)
                sources.insert(0, f"project:{rel}")
            except ValueError:
                sources.insert(0, f"project:{project_path}")
        # Project rules prepended so they are evaluated first
        self.rules = project_rules + user_rules
        self._file_sources = sources
        self._active_cache = None

    def _load_file(self, path: Path) -> tuple[List[Dict[str, Any]], bool]:
        """Return ``(rules, loaded)``.

        ``loaded`` is ``True`` only when the file actually existed and
        parsed cleanly — even if the rule list inside is empty. A
        non-existent file or unreadable/malformed JSON returns
        ``loaded=False`` so :meth:`active_permissions` can report only
        sources that were genuinely consulted.
        """
        if not path.exists():
            return [], False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("rules", []), True
        except (IOError, json.JSONDecodeError):
            return [], False

    def decide(self, tool_name: str, tool_args: Dict[str, Any]) -> Optional[PermissionDecision]:
        """Evaluate rules for a tool call.

        Evaluation order (first match wins):
          - full-access / plan mode: mode preset rules run first (can't be overridden)
          - all other modes: project JSON → user JSON → mode preset rules

        Returns:
            PermissionDecision.ALLOW / DENY / ASK for the first matching rule,
            or None if no rule matches.
        """
        detail = self.decide_detail(tool_name, tool_args)
        return detail.decision if detail is not None else None

    def decide_detail(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Optional[PermissionDecisionDetail]:
        """Same evaluation as :meth:`decide`, plus the matched rule.

        Returns ``None`` when no rule matches (the runtime falls back to
        the tool's own ``requires_confirmation`` attribute). The
        ``reason`` field is a short, stable string suitable for
        projection into a public event ``reason`` field.
        """
        if self.active_mode in (PermissionMode.FULL_ACCESS, PermissionMode.PLAN):
            rule_order = self._mode_rules + self.rules
        else:
            rule_order = self.rules + self._mode_rules
        for rule in rule_order:
            if self._matches(rule, tool_name, tool_args):
                action = rule.get("action", "ask").lower()
                if action == "allow":
                    return PermissionDecisionDetail(
                        PermissionDecision.ALLOW,
                        matched_rule=rule,
                        reason=f"matched rule for tool={rule.get('tool', '*')}",
                    )
                elif action == "deny":
                    return PermissionDecisionDetail(
                        PermissionDecision.DENY,
                        matched_rule=rule,
                        reason=f"deny rule for tool={rule.get('tool', '*')}",
                    )
                else:
                    return PermissionDecisionDetail(
                        PermissionDecision.ASK,
                        matched_rule=rule,
                        reason=f"ask rule for tool={rule.get('tool', '*')}",
                    )
        return None

    def _matches(self, rule: Dict[str, Any], tool_name: str, tool_args: Dict[str, Any]) -> bool:
        rule_tool = rule.get("tool", "*")
        if rule_tool != "*" and not self._match_pattern(rule_tool, tool_name):
            return False
        # Domain-based matching (for web_fetch and similar URL tools)
        domain_spec = rule.get("domain")
        if domain_spec is not None:
            url_arg = domain_spec.get("url_arg", "url")
            raw_url = str(tool_args.get(url_arg, ""))
            hostname = _extract_domain(raw_url)
            if hostname is None:
                return False  # unparseable URL never matches a domain rule
            allowlist = domain_spec.get("allowlist")
            blocklist = domain_spec.get("blocklist")
            if allowlist and _domain_matches(hostname, allowlist):
                return True
            if blocklist and _domain_matches(hostname, blocklist):
                return True
            return False  # domain rule present but no match
        # Regex-based arg matching
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

    def active_permissions(self) -> "ActivePermissions":
        """Return a JSON-safe :class:`ActivePermissions` snapshot.

        The result is cached and invalidated by :meth:`set_mode` and
        :meth:`add_loaded_source`. Permission decisions may invoke this
        on the tool execution hot path, so the implementation must not
        re-read disk on every call.

        Source order: preset first, then custom rules — i.e. the same
        order :meth:`decide` evaluates them. ``rules`` is a deep copy so
        callers cannot mutate the engine's internal state through the
        returned snapshot.
        """
        if self._active_cache is not None:
            return self._active_cache
        # Lazy import to keep ``agentao.permissions`` free of a hard
        # dependency on the harness package at module-load time.
        from .harness.models import ActivePermissions
        loaded_sources = [f"preset:{self.active_mode.value}"]
        loaded_sources.extend(self._file_sources)
        loaded_sources.extend(self._injected_sources)
        # Dedupe defensively while preserving order.
        seen: set = set()
        deduped = [s for s in loaded_sources if not (s in seen or seen.add(s))]
        # Mirror :meth:`decide`'s rule-evaluation order so a host that
        # walks ``rules`` sees the same precedence semantics as the
        # engine itself.
        if self.active_mode in (PermissionMode.FULL_ACCESS, PermissionMode.PLAN):
            ordered_rules = self._mode_rules + self.rules
        else:
            ordered_rules = self.rules + self._mode_rules
        snapshot = ActivePermissions(
            mode=self.active_mode.value,  # type: ignore[arg-type]
            rules=copy.deepcopy(ordered_rules),
            loaded_sources=deduped,
        )
        self._active_cache = snapshot
        return snapshot

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
                domain = rule.get("domain")
                if domain:
                    if "allowlist" in domain:
                        line += f"\n        domain allowlist: {', '.join(domain['allowlist'])}"
                    if "blocklist" in domain:
                        line += f"\n        domain blocklist: {', '.join(domain['blocklist'])}"
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
