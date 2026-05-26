"""Pydantic models for the non-interactive ``agentao run`` subcommand.

The models here form the structured contract between automation
callers and the run pipeline:

- :class:`RunSpec` — the YAML/JSON spec accepted on stdin or via
  ``--spec``. ``extra="forbid"`` so unknown fields fail loudly (exit 2).
- :class:`RunPermissionRule` — spec-level permission rule. Spec writers
  never author the ``action`` field; it is injected by
  :meth:`RunPermissionRule.to_engine_dict`.
- :class:`RunResult` — machine-readable run result envelope. Tolerant
  of extra fields on the consumer side (``extra="ignore"``) so that
  forward-compatible field additions don't break older parsers.

Action injection lives in exactly one place
(:meth:`RunPermissionRule.to_engine_dict`) so the engine never sees a
spec rule that authored its own ``action`` — that way ``extra="forbid"``
on :class:`RunPermissionRule` can flatly reject ``action:`` written by
hand in a spec file.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Permission rules (spec-side)
# ---------------------------------------------------------------------------


class RunPermissionDomainRule(BaseModel):
    """Domain-rule shape for spec permission entries."""

    url_arg: Optional[str] = None
    allowlist: Optional[List[str]] = None
    blocklist: Optional[List[str]] = None

    model_config = ConfigDict(extra="forbid")

    def to_engine_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.url_arg is not None:
            out["url_arg"] = self.url_arg
        if self.allowlist is not None:
            out["allowlist"] = list(self.allowlist)
        if self.blocklist is not None:
            out["blocklist"] = list(self.blocklist)
        return out


class RunPermissionRule(BaseModel):
    """One permission rule from the spec.

    Spec writers MUST NOT author the ``action`` field; the run pipeline
    injects ``allow`` or ``deny`` via :meth:`to_engine_dict` based on
    which list (``permissions.allow`` / ``permissions.deny``) the rule
    appeared in. ``extra="forbid"`` rejects ``action:`` written by hand.
    """

    tool: str
    args: Optional[Dict[str, Any]] = None
    domain: Optional[RunPermissionDomainRule] = None

    model_config = ConfigDict(extra="forbid")

    def to_engine_dict(self, action: Literal["allow", "deny"]) -> Dict[str, Any]:
        rule: Dict[str, Any] = {"tool": self.tool, "action": action}
        if self.args is not None:
            rule["args"] = dict(self.args)
        if self.domain is not None:
            rule["domain"] = self.domain.to_engine_dict()
        return rule


class RunPermissionRules(BaseModel):
    """Container for spec-level allow / deny rule lists."""

    allow: List[RunPermissionRule] = Field(default_factory=list)
    deny: List[RunPermissionRule] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Output / interaction
# ---------------------------------------------------------------------------


class RunOutputOptions(BaseModel):
    """``output:`` block in the run spec."""

    format: Optional[Literal["text", "json"]] = None

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Top-level run spec
# ---------------------------------------------------------------------------


PermissionModeName = Literal[
    "read-only", "workspace-write", "full-access", "plan",
]
InteractionPolicy = Literal["reject"]


# ASCII identifier rule for parameter names: matches Python identifier
# syntax so Jinja `{{ name }}` resolves cleanly. Reused by the CLI
# ``--param`` parser to give a uniform error message.
_PARAMETER_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Names Jinja2 reserves at the template-language level. Accepting any
# of these as a parameter name produces wrong behavior at render time:
# the constants (``true`` / ``True`` / ``false`` / ``False`` /
# ``none`` / ``None``) silently win over context variables (so
# ``{{ true }}`` resolves to Jinja's boolean, not the user's value),
# and the keywords (``for`` / ``if`` / ``in`` / ...) produce template
# syntax errors when referenced in ``{{ }}``. The list mirrors
# ``jinja2.lexer.keywords`` and the literal-constant tokens — these
# are stable across Jinja 3.x. Source isn't imported because the
# attribute is undocumented internal API.
_JINJA_RESERVED_NAMES: frozenset[str] = frozenset({
    "True", "False", "None", "true", "false", "none",
    "and", "as", "do", "else", "elif", "for", "if", "in", "is",
    "not", "or", "recursive", "with", "without",
    "block", "endblock", "call", "endcall", "endextends", "extends",
    "endfilter", "endfor", "endif", "endmacro", "endset", "endwith",
    "filter", "from", "import", "include", "macro", "set",
    # Runtime-injected context names — Jinja overwrites these on every
    # render (``self`` → TemplateReference, ``parent`` → block parent).
    # Accepting them silently discards the supplied value.
    "self", "parent",
})


class RunParameter(BaseModel):
    """One string-typed parameter slot for spec-level Jinja substitution.

    v1 supports string parameters only. ``number`` / ``boolean`` typing
    is deferred until a consumer drives coercion-rule decisions.
    """

    name: str
    required: bool = False
    default: Optional[str] = None
    choices: Optional[List[str]] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _name_must_be_identifier(cls, v: str) -> str:
        # Without this, Jinja parses ``{{ pr-number }}`` as subtraction
        # and chokes on whitespace in names like ``foo bar``.
        if not _PARAMETER_NAME_RE.fullmatch(v):
            raise ValueError(
                f"parameter name {v!r} must be an ASCII identifier "
                "(matching [A-Za-z_][A-Za-z0-9_]*)"
            )
        # Names like ``true`` / ``for`` pass the identifier regex but
        # break Jinja templating — see ``_JINJA_RESERVED_NAMES``.
        if v in _JINJA_RESERVED_NAMES:
            raise ValueError(
                f"parameter name {v!r} is reserved by Jinja2 "
                "(constants and keywords cannot be parameter names)"
            )
        return v

    @model_validator(mode="after")
    def _required_and_default_are_exclusive(self) -> "RunParameter":
        if self.required and self.default is not None:
            raise ValueError(
                f"parameter '{self.name}' cannot be both required and defaulted"
            )
        return self

    @model_validator(mode="after")
    def _default_must_be_in_choices(self) -> "RunParameter":
        if (
            self.default is not None
            and self.choices is not None
            and self.default not in self.choices
        ):
            raise ValueError(
                f"parameter '{self.name}' default {self.default!r} is "
                f"not in choices {self.choices}"
            )
        return self


class RunSpec(BaseModel):
    """Structured spec for a single ``agentao run`` invocation.

    All fields are optional at the model level because the CLI may
    supply them via flags. The pipeline validates required fields
    (``prompt``) post-merge and exits ``2`` if they are missing.
    """

    prompt: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    permission_mode: Optional[PermissionModeName] = None
    interaction_policy: Optional[InteractionPolicy] = None
    permissions: Optional[RunPermissionRules] = None
    max_iterations: Optional[int] = None
    skills: Optional[List[str]] = None
    replay: Optional[bool] = None
    output: Optional[RunOutputOptions] = None
    instructions: Optional[str] = None
    parameters: Optional[List[RunParameter]] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _no_duplicate_parameter_names(self) -> "RunSpec":
        if self.parameters:
            seen: set[str] = set()
            for p in self.parameters:
                if p.name in seen:
                    raise ValueError(f"duplicate parameter '{p.name}'")
                seen.add(p.name)
        return self


# ---------------------------------------------------------------------------
# Run result envelope
# ---------------------------------------------------------------------------


class RunErrorEnvelope(BaseModel):
    """Error block of :class:`RunResult` for non-success outcomes."""

    type: Literal[
        "permission_required",
        "permission_denied",
        "interaction_required",
        "max_iterations",
        "runtime_error",
        "invalid_spec",
        "interrupted",
    ]
    message: str
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    matched_rule: Optional[Dict[str, Any]] = None
    # ``ask_user`` raises ``interaction_required`` and the transport
    # records the prompt text the agent wanted to ask. The envelope
    # carries that text through so automation can decide what to do.
    question: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class RunUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    model_config = ConfigDict(extra="ignore")


class RunResult(BaseModel):
    """Machine-readable result emitted by ``agentao run --format json``.

    ``extra="ignore"`` on consumers is the documented stance for forward
    compatibility — older clients silently drop newly added fields
    rather than failing.
    """

    status: Literal["ok", "error"]
    session_id: str
    turn_id: Optional[str] = None
    cwd: str
    model: str
    final_text: Optional[str] = None
    error: Optional[RunErrorEnvelope] = None
    replay_path: Optional[str] = None
    usage: Optional[RunUsage] = None
    tool_calls: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


__all__ = [
    "InteractionPolicy",
    "PermissionModeName",
    "RunErrorEnvelope",
    "RunOutputOptions",
    "RunParameter",
    "RunPermissionDomainRule",
    "RunPermissionRule",
    "RunPermissionRules",
    "RunResult",
    "RunSpec",
    "RunUsage",
]
