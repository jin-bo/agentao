"""Declarative permission rule engine for tool execution control.

The hardline shell-safety scanner that pre-empts unrecoverable
operations lives in :mod:`agentao.permissions_hardline`; this module
imports its single entry point :func:`hardline_check` and wraps any
non-``None`` return into a DENY decision before rule evaluation.
"""

import copy
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
from urllib.parse import urlparse

from .permissions_hardline import hardline_check

if TYPE_CHECKING:
    from .host.models import ActivePermissions


class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# Maps the rule "action" string (allow/deny/ask) to the decision enum.
# Unknown actions fall through to ASK — the safe-by-default behavior the
# engine has always had.
_ACTION_TO_DECISION: Dict[str, PermissionDecision] = {
    "allow": PermissionDecision.ALLOW,
    "deny": PermissionDecision.DENY,
    "ask": PermissionDecision.ASK,
}

# Stable policy-source prefixes for ``PermissionDecisionDetail.reason``.
# Hosts and audit displays may rely on these prefixes; they are part of
# the public event contract once a ``PermissionDecisionEvent`` is emitted
# with the ``reason`` field. The ``hardline`` prefix lives in
# :mod:`.permissions_hardline` (re-exported as ``REASON_HARDLINE``).
_REASON_MODE_PRESET = "mode-preset"
_REASON_USER_RULE = "user-rule"
# Pre-check tier for spec-injected deny rules. Evaluated after
# hardline but before every other source so a per-run deny cannot be
# shadowed by a user/project ``allow:*`` or a preset ``allow:*``.
_REASON_INJECTED = "injected"


# ---------------------------------------------------------------------------
# Rule validation
# ---------------------------------------------------------------------------
#
# The engine reads exactly four keys off a rule (``tool``, ``action``,
# ``args``, ``domain``) and checks the type of none of them, so until this
# validator existed a one-word typo survived all the way to the permission
# hot path. ``{"tool": "run_shell_command", "pattern": "^git ",
# "action": "allow"}`` loses its condition — the key is ``args`` — and
# widens into a *tool-wide* allow that ``/permissions`` renders as an
# ordinary ``[✓ ALLOW]``. The type failures are not smaller, only worse
# timed: six of the seven raise ``AttributeError``/``TypeError`` out of
# :meth:`PermissionEngine.decide_detail` mid-turn, at the first tool call,
# and the seventh (``domain.allowlist`` as a string) silently degrades a
# ``deny`` to ASK.
#
# The validator is deliberately **pure**, and deliberately takes a rule
# *list* rather than a parsed JSON document: two of its three callers
# (``PermissionEngine(rules=...)`` and :meth:`add_run_rules`) are never
# handed a document at all. Document shape — ``{"rules": [...]}``, the
# top-level object check, the file path in the message — belongs to
# :mod:`agentao.embedding.permission_loader`, the only layer that reads
# documents and the only one that knows a path.

# Both key sets are closed: the engine ignores anything else in silence,
# which is exactly the failure mode above.
_LEGAL_RULE_FIELDS: Tuple[str, ...] = ("action", "args", "dialect", "domain", "tool")

# TOOL-02. The label names a *dialect*, never a rung: `posix` covers both the Git Bash rung
# and the system shell, because what a regular expression can read is decided by the syntax,
# not by which interpreter was selected. A rule written for bash, applied to PowerShell text,
# neither allows nor denies the right thing — it simply fails to match, and a floor that
# fails to match reports clean.
_LEGAL_DIALECTS: Tuple[str, ...] = ("posix", "cmd", "powershell", "*")
_LEGAL_DOMAIN_FIELDS: Tuple[str, ...] = ("allowlist", "blocklist", "url_arg")

# Absence is as dangerous as a typo here: the engine's fallbacks for these
# two keys (``"*"`` and ``"ask"``) silently rewrite the rule rather than
# refuse it, so a closed key set alone would leave the escalation open.
_REQUIRED_RULE_FIELDS: Tuple[str, ...] = ("tool", "action")

# ``(index, reason)``. ``index`` is ``None`` for the one failure with no
# rule ordinal — the collection itself is not a list.
RuleError = Tuple[Optional[int], str]


class PermissionRuleError(ValueError):
    """Raised when host-supplied permission rules fail validation.

    Carries the structured ``(index, reason)`` failures so a caller that
    knows more about provenance than this module does can re-render them.
    File-sourced rules raise
    :class:`agentao.embedding.permission_loader.PermissionConfigError`
    instead, which adds the path.
    """

    def __init__(self, errors: List[RuleError], *, subject: str = "rules") -> None:
        self.errors: List[RuleError] = list(errors)
        self.subject = subject
        super().__init__(
            "Invalid permission rules:"
            + format_permission_rule_errors(self.errors, subject=subject)
        )


def _typename(value: Any) -> str:
    return type(value).__name__


def format_permission_rule_errors(
    errors: List[RuleError], *, subject: str = "rules",
) -> str:
    """Render ``(index, reason)`` pairs as an indented bullet list.

    ``subject`` names the list the indices address — ``"rules"`` for a
    file or a ``rules=`` kwarg, ``"permissions.allow"`` /
    ``"permissions.deny"`` for :meth:`PermissionEngine.add_run_rules`,
    whose two inputs are validated separately so each index stays
    meaningful to the caller that wrote them.
    """
    return "".join(
        "\n  - "
        + (f"{subject}: " if index is None else f"{subject}[{index}]: ")
        + reason
        for index, reason in errors
    )


def validate_permission_rules(rules: Any) -> List[RuleError]:
    """Validate a permission **rule list**. Returns ``[]`` when valid.

    Args:
        rules: The candidate rule list. Any object is accepted — a
            non-list is itself a reported failure, so callers never need
            a type check of their own.

    Returns:
        A list of ``(index, reason)`` pairs, one per distinct problem;
        empty means valid. ``index`` is the rule's ordinal, or ``None``
        when ``rules`` is not a list at all.
    """
    if not isinstance(rules, list):
        return [(None, f"expected a list of rules, got {_typename(rules)}")]
    errors: List[RuleError] = []
    for index, rule in enumerate(rules):
        errors.extend((index, reason) for reason in _rule_errors(rule))
    return errors


def _require_valid_rules(rules: Any, *, subject: str = "rules") -> None:
    """Raise :class:`PermissionRuleError` if ``rules`` does not validate."""
    errors = validate_permission_rules(rules)
    if errors:
        raise PermissionRuleError(errors, subject=subject)


def _rule_errors(rule: Any) -> List[str]:
    if not isinstance(rule, dict):
        return [f"rule must be an object, got {_typename(rule)}"]
    reasons: List[str] = []
    # Presence, not just type. ``configuration.md`` §4 marks both fields
    # required, and the engine's defaults for a *missing* key are exactly
    # the widening this validator exists to stop: ``rule.get("tool", "*")``
    # turns ``{"action": "allow"}`` into an allow-**everything** rule, and
    # ``rule.get("action", "ask")`` quietly re-labels a rule the author
    # meant as a deny.
    for required in _REQUIRED_RULE_FIELDS:
        if required not in rule:
            reasons.append(
                f"missing required field {required!r}"
                + (
                    " (use {\"tool\": \"*\"} for a deliberate wildcard)"
                    if required == "tool" else ""
                )
            )
    # ``key=repr`` because a host can pass a dict with non-string keys,
    # and sorting those against ``str`` raises TypeError.
    for key in sorted((k for k in rule if k not in _LEGAL_RULE_FIELDS), key=repr):
        reasons.append(
            f"unknown field {key!r} "
            f"(allowed: {', '.join(_LEGAL_RULE_FIELDS)})"
        )
    if "tool" in rule and not isinstance(rule["tool"], str):
        reasons.append(f"'tool' must be a string, got {_typename(rule['tool'])}")
    if "action" in rule:
        reasons.extend(_action_errors(rule["action"]))
    if "args" in rule:
        reasons.extend(_args_errors(rule["args"]))
    if "domain" in rule:
        reasons.extend(_domain_errors(rule["domain"]))
    if "dialect" in rule:
        reasons.extend(_dialect_errors(rule["dialect"]))
    return reasons


def _dialect_errors(dialect: Any) -> List[str]:
    if not isinstance(dialect, str):
        return [f"'dialect' must be a string, got {_typename(dialect)}"]
    if dialect.lower() not in _LEGAL_DIALECTS:
        return [
            f"unknown dialect {dialect!r} (allowed: {', '.join(_LEGAL_DIALECTS)})"
        ]
    return []


def rule_matches_dialect(rule: Dict[str, Any], dialect: Optional[str]) -> bool:
    """TOOL-02: whether a rule applies to the dialect this call will run in.

    An unlabelled rule matches everything, which keeps every rule written before the label
    existed working exactly as it did. That permissiveness is the reason for the other half
    of TOOL-02: an unlabelled rule carrying an ``args.command`` condition is *unspecified*
    rather than universal, and a PowerShell rung refuses to be built while one exists.

    A *labelled* rule against an **unknown** dialect does not match. The label is its author
    saying "this pattern is written for that language"; when nothing has said which language
    this call runs in, applying it anyway is the same guess in the other direction — and an
    ``allow`` labelled ``powershell`` would then grant every call on a POSIX host, which is
    the failure the label exists to prevent. ``dialect`` is ``None`` for every tool that is
    not the shell and for a spec that could not be resolved.
    """
    label = rule.get("dialect")
    if not isinstance(label, str) or label == "*":
        return True
    return dialect is not None and label.lower() == dialect.lower()


def unspecified_shell_rules(rules: Any) -> List[Tuple[int, Dict[str, Any]]]:
    """TOOL-02: the rules a PowerShell rung cannot be built alongside.

    A rule that matches on ``args.command`` was written against *some* shell's syntax, and
    which one is not recorded anywhere. On POSIX and cmd it keeps working, because that is
    what it has always done and this design does not break configurations that predate it.
    On PowerShell there is no safe reading: applying it is applying a pattern to a language
    it was not written for, and skipping it silently drops a rule its author is relying on.
    So the rung refuses to exist, naming every such rule and all four labels.
    """
    offenders: List[Tuple[int, Dict[str, Any]]] = []
    if not isinstance(rules, list):
        return offenders
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or "dialect" in rule:
            continue
        args = rule.get("args")
        if isinstance(args, dict) and "command" in args:
            offenders.append((index, rule))
    return offenders


def _action_errors(action: Any) -> List[str]:
    if not isinstance(action, str):
        return [f"'action' must be a string, got {_typename(action)}"]
    # Case-insensitive by contract (docs/reference/configuration.md §4);
    # unknown values are rejected rather than silently treated as "ask",
    # which is what left ``{"action": "alow"}`` inert.
    if action.lower() not in _ACTION_TO_DECISION:
        return [
            f"'action' must be one of {', '.join(_ACTION_TO_DECISION)} "
            f"(case-insensitive), got {action!r}"
        ]
    return []


def _args_errors(args: Any) -> List[str]:
    if not isinstance(args, dict):
        return [f"'args' must be an object, got {_typename(args)}"]
    reasons: List[str] = []
    for key in sorted(args, key=repr):
        if not isinstance(key, str):
            reasons.append(f"'args' keys must be strings, got {_typename(key)}")
        elif not isinstance(args[key], str):
            reasons.append(
                f"'args.{key}' must be a regex string, got {_typename(args[key])}"
            )
    return reasons


def _domain_errors(domain: Any) -> List[str]:
    if not isinstance(domain, dict):
        return [f"'domain' must be an object, got {_typename(domain)}"]
    reasons: List[str] = []
    for key in sorted((k for k in domain if k not in _LEGAL_DOMAIN_FIELDS), key=repr):
        reasons.append(
            f"unknown field {key!r} under 'domain' "
            f"(allowed: {', '.join(_LEGAL_DOMAIN_FIELDS)})"
        )
    if "url_arg" in domain and not isinstance(domain["url_arg"], str):
        reasons.append(
            f"'domain.url_arg' must be a string, got {_typename(domain['url_arg'])}"
        )
    for field in ("allowlist", "blocklist"):
        if field not in domain:
            continue
        entries = domain[field]
        # A bare string here is the quiet one: ``_domain_matches`` iterates
        # it character by character, nothing matches, and the rule stops
        # firing — a deny degrades to ASK with no error anywhere.
        if not isinstance(entries, list):
            reasons.append(
                f"'domain.{field}' must be a list of strings, got "
                f"{_typename(entries)}"
            )
            continue
        for position, entry in enumerate(entries):
            if not isinstance(entry, str):
                reasons.append(
                    f"'domain.{field}[{position}]' must be a string, got "
                    f"{_typename(entry)}"
                )
    return reasons


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


# Shell-rc and credential-file writes via redirection / mover utilities.
# Mode-scoped to ``workspace-write`` and emits ASK — installers (Homebrew,
# pyenv, rustup) and devops scripts legitimately edit these files.
# ``full-access`` deliberately doesn't carry this rule: that mode promises
# literal full access, and disk-wipe-class attacks already trip the hardline
# floor.
#
# Known coverage gap (a ``bashlex`` pass would close it): variable
# indirection (``dst=~/.bashrc; echo X > "$dst"``), process-substitution
# wrappers, and literal expanded paths (``/Users/<u>/.bashrc``) that need
# user enumeration to detect.
_SHELL_SENSITIVE_FILE_RE = (
    r"(?:~|\$HOME|\$\{HOME\})/"
    r"\.(?:bashrc|zshrc|profile|bash_profile|zprofile|netrc|pgpass|npmrc|pypirc)"
    # Strict terminator — ``\b`` would match ``~/.bashrc.bak`` and
    # ``~/.bashrc-old`` because ``c`` ↔ ``.``/``-`` is a word boundary.
    r"(?=\s|[;|&)>'\"<]|$)"
)

_SHELL_SENSITIVE_WRITE_RE = "(?:" + "|".join([
    # Redirect (``>``, ``>>``, optional space). ``re.search`` finds the ``>``
    # anywhere in the command, so an FD prefix like ``2>`` is handled
    # without an explicit token here.
    rf">>?\s*{_SHELL_SENSITIVE_FILE_RE}",
    rf"\btee\b(?:\s+-\S+)*\s+{_SHELL_SENSITIVE_FILE_RE}",
    rf"\b(?:cp|mv)\b(?:\s+-\S+)*\s+\S+\s+{_SHELL_SENSITIVE_FILE_RE}",
    # Require the literal ``-i`` / ``-i.bak`` token before the file. Lazy
    # ``\S+`` quantifiers let the engine backtrack to the trailing file.
    rf"\bsed\b(?:\s+\S+)*?\s+-i(?:\.\S+)?(?:\s+\S+)*?\s+{_SHELL_SENSITIVE_FILE_RE}",
]) + ")"


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
        # ASK — not DENY — so installers/devops scripts can proceed with
        # operator confirmation. Inspectable via ``active_permissions()`` so
        # a host UI can render "shell-rc writes will prompt" without
        # reverse-engineering the engine.
        {
            "tool": "run_shell_command",
            "args": {"command": _SHELL_SENSITIVE_WRITE_RE},
            "action": "ask",
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

    Rules are sourced from the user scope (``<user_root>/permissions.json``).
    Hosts may inject additional policy via :meth:`add_loaded_source`.

    Project-scope ``.agentao/permissions.json`` is intentionally NOT
    loaded: a checked-in rule could grant the agent capabilities the
    user never approved (e.g. ``{"tool": "*", "action": "allow"}``
    inside a cloned repo would defeat the entire user policy because
    the engine returns on the first matching rule). Permissions are a
    user/host concern, not a cwd concern — the same model OS
    permissions and IDE workspace-trust use. If such a file exists, it
    is ignored with a warning.

    **Two construction modes.** First-party callers pre-load rules via
    :func:`agentao.embedding.permission_loader.load_permission_rules`
    and pass them explicitly with ``rules=`` / ``loaded_sources=``; the
    engine then performs **no disk I/O** of its own. Legacy callers
    that pass only ``project_root`` / ``user_root`` still work — the
    engine lazy-delegates to the embedding loader to fill in the
    rules — but the file I/O is no longer part of this module.

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
        rules: Optional[List[Dict[str, Any]]] = None,
        loaded_sources: Optional[List[str]] = None,
        enable_hardline: bool = True,
    ):
        """Initialize the permission engine.

        Args:
            project_root: Project directory. Used (a) to detect and
                warn on a stray ``<project_root>/.agentao/permissions.json``
                on the legacy auto-load path, and (b) as a label
                attribute on the engine. Required so the warning's
                path is self-explanatory.
            user_root: Optional user-scope directory whose
                ``<user_root>/permissions.json`` is loaded on the
                legacy auto-load path. Ignored when ``rules=`` is
                passed (the caller has already loaded). ``None`` on
                the auto-load path skips the user-scope read.
            rules: Pre-loaded rule list. When provided (the
                recommended path), the engine treats it as the sole
                file-source rule list and does **not** read disk.
                Pair with ``loaded_sources`` so
                :meth:`active_permissions` reports correct provenance.
                Use :func:`agentao.embedding.permission_loader.load_permission_rules`
                to produce both values.
            loaded_sources: Source labels (e.g. ``"user:/path/to/permissions.json"``)
                that match ``rules``. Defaults to an empty list when
                ``rules`` is provided without explicit sources.
            enable_hardline: When ``True`` (the default), a small set
                of *unrecoverable* operations (rm -rf /, mkfs, dd to
                raw block devices, fork bombs, shutdown / reboot, …)
                are denied before any rule is consulted — including
                ``full-access``. Embedded hosts that take policy
                responsibility themselves (typically because Agentao
                is sandboxed in a container or the host has its own
                deny pipeline) can pass ``False`` to make
                ``full-access`` literally mean full access.
        """
        if project_root is None:
            raise TypeError(
                "PermissionEngine requires a project_root keyword argument."
            )
        self._user_root: Optional[Path] = user_root
        self._enable_hardline: bool = enable_hardline
        self._mode_rules: List[Dict[str, Any]] = []
        self.active_mode: PermissionMode = PermissionMode.WORKSPACE_WRITE
        # ``injected:*`` entries are appended by hosts via
        # :meth:`add_loaded_source`. Preset source is composed
        # dynamically from ``active_mode`` so a mode switch is
        # reflected without re-reading disk.
        self._injected_sources: List[str] = []
        # Cached :class:`ActivePermissions` projection. Invalidated by
        # mode switches and source-list mutations so the permission
        # decision hot path can call ``active_permissions()`` cheaply.
        self._active_cache: Optional["ActivePermissions"] = None

        if rules is not None:
            # Host-supplied rules go through the same validator as the
            # file path; there is no path to name, so the failure is a
            # plain PermissionRuleError raised at the constructor.
            _require_valid_rules(rules)
            self.rules: List[Dict[str, Any]] = list(rules)
            self._file_sources: List[str] = list(loaded_sources or [])
        else:
            # Lazy import: avoid module-load dep on agentao.embedding.
            from .embedding.permission_loader import load_permission_rules
            self.rules, self._file_sources = load_permission_rules(
                project_root=project_root, user_root=user_root,
            )

        # Spec-injected deny rules — see :meth:`add_run_rules`. Spec
        # allow rules join ``self.rules`` and follow the standard
        # per-mode ordering instead.
        self._run_scope_rules: List[Dict[str, Any]] = []

        self._mode_rules = _PRESET_RULES[self.active_mode.value]

    def set_mode(self, mode: PermissionMode) -> None:
        """Switch the active permission preset. Mode rules are evaluated after project/user rules."""
        self.active_mode = mode
        self._mode_rules = _PRESET_RULES[mode.value]
        self._active_cache = None

    def add_run_rules(
        self,
        *,
        allow: Optional[List[Dict[str, Any]]] = None,
        deny: Optional[List[Dict[str, Any]]] = None,
        source: str = "run-spec",
    ) -> None:
        """Inject per-run permission rules.

        ``deny`` rules go into the pre-check tier
        (:attr:`_run_scope_rules`) evaluated after hardline but before
        any other source in every mode, so a per-run restriction
        cannot be shadowed by a project/user ``allow:*`` or a preset
        ``allow:*``. ``allow`` rules append to the standard user-rule
        list and follow the engine's existing per-mode ordering —
        they do **not** create a new priority tier.

        Both lists must already be in engine dict shape; the caller
        injects the ``action`` field (see
        :meth:`agentao.cli.run_models.RunPermissionRule.to_engine_dict`).

        Provenance is recorded once under ``"injected:<source>"``
        (default ``"injected:run-spec"``).
        """
        # Validated per-list so a reported index maps back to the
        # ``permissions.allow`` / ``permissions.deny`` block the spec
        # author actually wrote.
        if deny is not None:
            _require_valid_rules(deny, subject="permissions.deny")
        if allow is not None:
            _require_valid_rules(allow, subject="permissions.allow")
        if deny:
            self._run_scope_rules.extend(deny)
        if allow:
            self.rules.extend(allow)
        if allow or deny:
            self.add_loaded_source(f"injected:{source}")
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

    def decide(self, tool_name: str, tool_args: Dict[str, Any]) -> Optional[PermissionDecision]:
        """Evaluate rules for a tool call.

        Evaluation order (first match wins):
          - full-access / plan mode: mode preset rules run first (can't be overridden)
          - all other modes: user JSON rules → mode preset rules

        Returns:
            PermissionDecision.ALLOW / DENY / ASK for the first matching rule,
            or None if no rule matches.
        """
        detail = self.decide_detail(tool_name, tool_args)
        return detail.decision if detail is not None else None

    @property
    def hardline_enabled(self) -> bool:
        """Whether the floor is consulted at all.

        Read by the planner, which computes this call's frozen record before the engine sees
        it: a host that disabled the floor must not get shell denials through the record
        instead. Exposed rather than reached for, so the two readers are reading one flag.
        """
        return self._enable_hardline

    def decide_detail(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        *,
        shell_spec: Any = None,
        decided: Any = None,
    ) -> Optional[PermissionDecisionDetail]:
        """Same evaluation as :meth:`decide`, plus the matched rule.

        Returns ``None`` when no rule matches (the runtime falls back to
        the tool's own ``requires_confirmation`` attribute). The
        ``reason`` field is a short, stable, **policy-source-tagged**
        string suitable for projection into a public event ``reason``
        field. Source prefixes:

        - ``hardline:<description>`` — opt-out floor refused the call
        - ``mode-preset:<rule_tool>`` — preset rule matched
        - ``user-rule:<rule_tool>`` — user JSON rule matched
        """
        # Hardline pre-check runs before mode/preset/user-rule routing
        # so a ``full-access`` ``allow:*`` rule cannot silently shadow
        # it. When the host has disabled the floor the caller has
        # accepted policy responsibility — fall through.
        if self._enable_hardline:
            reason = hardline_check(
                tool_name, tool_args, shell_spec=shell_spec, decided=decided,
            )
            if reason is not None:
                return PermissionDecisionDetail(
                    PermissionDecision.DENY,
                    matched_rule=None,
                    reason=reason,
                )

        # ``full-access`` / ``plan`` modes evaluate preset rules first
        # so a stray user rule cannot shadow the mode's promise; every
        # other mode evaluates user rules first so they can override.
        # ``_run_scope_rules`` (spec deny) always evaluates first so a
        # per-run restriction cannot be shadowed by any other source.
        if self.active_mode in (PermissionMode.FULL_ACCESS, PermissionMode.PLAN):
            sources: List[Tuple[List[Dict[str, Any]], str]] = [
                (self._run_scope_rules, f"{_REASON_INJECTED}:run-spec"),
                (self._mode_rules, _REASON_MODE_PRESET),
                (self.rules, _REASON_USER_RULE),
            ]
        else:
            sources = [
                (self._run_scope_rules, f"{_REASON_INJECTED}:run-spec"),
                (self.rules, _REASON_USER_RULE),
                (self._mode_rules, _REASON_MODE_PRESET),
            ]
        dialect = getattr(getattr(shell_spec, "dialect", None), "value", None)
        for rules, source in sources:
            for rule in rules:
                # TOOL-02 before anything else about the rule: a label that does not name
                # this call's dialect means the rule was written for a different language,
                # and a pattern from another language does not fail loudly here — it fails
                # to match, which reads as "no rule applied".
                if not rule_matches_dialect(rule, dialect):
                    continue
                if not self._matches(rule, tool_name, tool_args):
                    continue
                action = rule.get("action", "ask").lower()
                decision = _ACTION_TO_DECISION.get(action, PermissionDecision.ASK)
                return PermissionDecisionDetail(
                    decision,
                    matched_rule=rule,
                    reason=f"{source}:{rule.get('tool', '*')}",
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
        from .host.models import ActivePermissions
        loaded_sources = [f"preset:{self.active_mode.value}"]
        loaded_sources.extend(self._file_sources)
        loaded_sources.extend(self._injected_sources)
        # Dedupe defensively while preserving order.
        seen: set = set()
        deduped = [s for s in loaded_sources if not (s in seen or seen.add(s))]
        # Mirror :meth:`decide`'s rule-evaluation order. Spec deny
        # (``_run_scope_rules``) always sits in front because it
        # pre-checks every mode after hardline.
        if self.active_mode in (PermissionMode.FULL_ACCESS, PermissionMode.PLAN):
            ordered_rules = (
                self._run_scope_rules + self._mode_rules + self.rules
            )
        else:
            ordered_rules = (
                self._run_scope_rules + self.rules + self._mode_rules
            )
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
                "No custom rules. Create ~/.agentao/permissions.json to add rules.\n"
                "(Project-scope .agentao/permissions.json is no longer honored.)\n\n"
                "Example:\n"
                '  {"rules": [\n'
                '    {"tool": "run_shell_command", "args": {"command": "^git "}, "action": "allow"},\n'
                '    {"tool": "write_file", "action": "ask"},\n'
                '    {"tool": "*", "action": "ask"}\n'
                "  ]}"
            )
        return "\n".join(lines)
