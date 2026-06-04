# RunSpec Parameters & Instructions: Closing the Recipe Gap

**Status:** Shipped 2026-05-25. Implementation notes follow at the bottom of this doc.
**Audience:** agentao maintainers considering `agentao run` ergonomics and the
goose-recipes comparison.
**Companion:** `run-spec-parameters.zh.md`.

## Problem

Goose ships a "recipe" concept (`crates/goose/src/recipe/`): a single YAML file
declaring `prompt`, `instructions`, typed `parameters`, `settings`,
`extensions`, and an optional `response.json_schema`. Recipes are renderable
through MiniJinja and form goose's primary shareable, parameterized workflow
artifact.

A direct port would land a parallel "Recipes" concept next to `RunSpec` — a
second source of truth for "how to launch a non-interactive run." That is the
wrong shape for agentao because **`RunSpec` (`agentao/cli/run_models.py:111`)
already covers ~75% of recipe semantics**. The gap is narrow and additive.

This design fills the gap without introducing a second concept.

## Current coverage

Goose recipe field → `RunSpec` field, verified against
`agentao/cli/run_models.py:111-131`:

| Recipe field              | RunSpec field                          | Status |
|---------------------------|----------------------------------------|--------|
| `prompt`                  | `prompt`                               | ✅ |
| `settings.provider/model` | `model`, `base_url`                    | ✅ |
| `settings.max_turns`      | `max_iterations`                       | ✅ |
| `extensions.builtin`      | `skills`                               | ✅ |
| `extensions.mcp`          | `.agentao/mcp.json` (separate config)  | ✅ via reference |
| `permissions`             | `permissions` + `permission_mode`      | ✅ cleaner |
| (replay)                  | `replay`                               | ✅ ahead of goose |
| `instructions`            | —                                      | ❌ gap |
| `parameters` (typed)      | —                                      | ❌ gap |
| `activities` (UI pills)   | —                                      | skip (no UI) |
| `response.json_schema`    | —                                      | skip (separate design) |
| `title`, `description`    | —                                      | skip (no consumer yet) |

Two real gaps. `title`/`description` deferred until a `--list` command actually
wants them — adding speculative public schema now violates the project's
"don't design for hypothetical future requirements" rule.

## Proposal

Add two fields to `RunSpec` and one Jinja-rendering pass. No new file format,
no new CLI subcommand, no separate Recipes module.

### Pydantic model delta (`agentao/cli/run_models.py`)

```python
import re

from pydantic import (
    BaseModel, ConfigDict, Field, field_validator, model_validator,
)


class RunParameter(BaseModel):
    """One string-typed parameter slot for spec-level Jinja substitution."""

    name: str
    required: bool = False
    default: Optional[str] = None
    choices: Optional[List[str]] = None       # enum-style validation

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _name_must_be_identifier(cls, v: str) -> str:
        # ASCII identifier rule — Jinja `{{ name }}` would otherwise
        # parse 'pr-number' as subtraction and choke on 'foo bar'.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(
                f"parameter name {v!r} must be an ASCII identifier "
                "(matching [A-Za-z_][A-Za-z0-9_]*)"
            )
        return v

    @model_validator(mode="after")
    def _required_and_default_are_exclusive(self):
        if self.required and self.default is not None:
            raise ValueError(
                f"parameter '{self.name}' cannot be both required and defaulted"
            )
        return self

    @model_validator(mode="after")
    def _default_must_be_in_choices(self):
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
    # ... existing fields ...
    instructions: Optional[str] = None        # appended to system prompt
    parameters: Optional[List[RunParameter]] = None

    @model_validator(mode="after")
    def _no_duplicate_parameter_names(self):
        if self.parameters:
            seen: set[str] = set()
            for p in self.parameters:
                if p.name in seen:
                    raise ValueError(f"duplicate parameter '{p.name}'")
                seen.add(p.name)
        return self
```

**Also update `__all__` at `run_models.py:194`** to export `RunParameter`
alongside the existing entries — the module keeps a strict alphabetical
export list as its local contract.

These validators fire at spec-parse time (inside `_parse_spec_text`),
surfacing as `invalid_spec` / exit 2. Validation is structural so it
belongs on the models, not in the renderer.

`required: true` plus `default: ...` is rejected outright rather than
quietly making one win. A defaulted parameter is by definition not
required; allowing both invites implementation drift.

**v1 supports `string` parameters only.** `number` and `boolean` are deferred:
both need precise coercion rules (`true/false/1/0/yes/no`? `42.0` → int or
float? choices-comparison before or after coercion?) that aren't worth
designing without a real consumer. A string-only v1 covers the bulk of CLI
parameter patterns; we add types when someone needs them.

Both additions are optional; `extra="forbid"` continues to reject typos.
Existing specs validate unchanged — purely additive.

### Pipeline position

**`--param` values are not RunSpec fields.** They must not flow through
`_apply_cli_overrides` because `extra="forbid"` would reject them, and even
if it didn't, mixing "parameter declarations" with "parameter values" in the
same Pydantic model conflates two distinct concepts.

Two new helpers in `agentao/cli/run.py`:

```python
def _parse_cli_params(items: Optional[list[str]]) -> dict[str, str]:
    """Parse repeated --param KEY=VALUE arguments into a dict.

    Raises _UsageError on malformed entries or duplicate keys.
    """
```

`_execute_with_args` (`agentao/cli/run.py:315`) calls in this order:

1. `_load_spec(args)` — line 323 (unchanged).
2. `_apply_cli_overrides(spec, args)` — line 337 (unchanged).
3. **NEW** — must be wrapped to route into the existing
   `_emit_invalid_usage` path:
   ```python
   try:
       params = _parse_cli_params(args.params)
       spec = render_spec(spec, params)
   except (_UsageError, RunTemplateError) as exc:
       return _emit_invalid_usage(str(exc), output_format)
   ```
   `_parse_cli_params` raises `_UsageError` (already defined in `run.py`).
   `render_spec` raises a small `RunTemplateError(ValueError)` defined in
   `run_template.py` — keeps the exception surface explicit, lets the
   except clause name what it catches, and avoids accidentally swallowing
   unrelated `ValueError`s from Pydantic.
4. `if not spec.prompt: ...` — line 346 (unchanged; now checks the
   *rendered* prompt).
5. `_run_pipeline(spec, ...)` — line 359 (unchanged).

Render must come **after** `_apply_cli_overrides` (so `--prompt` overrides
can themselves be templates if the spec declares parameters) and **before**
the `prompt is required` check (so a template that resolves to an empty
string still fails the right way).

### Renderer trigger rule (three cases)

| `spec.parameters` | CLI `params` | Behavior |
|-------------------|--------------|----------|
| empty / None      | empty        | No-op. Jinja2 not invoked. Literal `{{ }}` in spec passes through. |
| empty / None      | non-empty    | exit 2 `invalid_spec` — first param name as `unknown parameter`. |
| non-empty         | any          | Run renderer (validate params, then template `prompt` + `instructions`). |

Rationale: silent no-op when the user supplied `--param` to a parameterless
spec would hide a real typo. The "spec has no parameters" path is reserved
for specs that don't use templating at all.

### Renderer details

`agentao/cli/run_template.py::render_spec(spec, params) -> RunSpec`:

1. Validates supplied params against declared `spec.parameters` (required
   check, choices enforcement, unknown-key check).
2. Renders `spec.prompt` and `spec.instructions` (only these two fields)
   through a sandboxed Jinja2 `Environment` with `undefined=StrictUndefined`.
3. Returns a new `RunSpec` with rendered strings.

Failure modes (all exit 2, `invalid_spec`):

- Missing required param → `agentao run: parameter '{name}' is required`.
- Unknown param → `agentao run: unknown parameter '{name}'`.
- Choices violation → `agentao run: parameter '{name}' must be one of {choices}`.
- Jinja undefined variable → `agentao run: template uses undefined variable
  '{name}' (declare it in spec.parameters)`.
- Jinja syntax error → `agentao run: template syntax error in spec.{field}: {msg}`.

`StrictUndefined` is non-negotiable — silent empty substitution would let
typos in the prompt template ship without error.

### Dependency

Jinja2 becomes a regular (not optional) dependency. The optional-dep approach
introduced a syntax-detection branch, lazy imports, and extra tests for a
tiny package (~1MB, no transitive native deps). Not worth the complexity.
Add to `pyproject.toml`'s base deps, import at the top of `run_template.py`,
done.

### CLI flag (`agentao/cli/run.py`)

One new flag on `add_run_subparser`:

```python
parser.add_argument(
    "--param", dest="params", action="append", default=None,
    metavar="KEY=VALUE",
    help="Set a spec parameter. Repeatable. Example: --param depth=deep",
)
```

`_parse_cli_params` rules (all errors are `_UsageError` → exit 2,
`invalid_spec`):

- **Missing `=`**: `--param foo` → `agentao run: malformed --param 'foo'
  (expected KEY=VALUE)`.
- **Empty key**: `--param =1` → same malformed message.
- **Multiple `=` in value**: `--param expr=a=b` → key=`expr`, value=`a=b`
  (split on the first `=` only; value is preserved verbatim).
- **Duplicate key**: `--param x=1 --param x=2` → `agentao run: --param 'x'
  supplied multiple times`. Erroring is less surprising than last-wins.
- **Non-identifier key**: `--param foo-bar=v` or `--param 1foo=v` →
  `agentao run: --param '{key}' is not a valid identifier (must match
  [A-Za-z_][A-Za-z0-9_]*)`. Same regex as the spec validator; failing
  here gives a clearer message than letting it fall through to "unknown
  parameter" downstream.

### Agent wiring (concrete)

`agentao/cli/run.py:404` currently builds `factory_kwargs` like this:

```python
factory_kwargs: Dict[str, Any] = dict(
    working_directory=cwd,
    transport=transport,
    replay_config=replay_config,
)
if spec.model is not None:
    factory_kwargs["model"] = spec.model
if spec.base_url is not None:
    factory_kwargs["base_url"] = spec.base_url
```

Add one line in the same block:

```python
if spec.instructions is not None:
    factory_kwargs["project_instructions"] = spec.instructions
```

This routes through the kwarg already at `agentao/agent.py:68`, which
short-circuits the AGENTAO.md disk read (`agent.py:349-352`). **Precedence is
implicit**: when `spec.instructions` is set, AGENTAO.md is skipped; when it
is not set, AGENTAO.md is read normally. No warning, no extra detection
logic, no coupling to disk-presence checks.

## Worked example

```yaml
# .agentao/runs/review-pr.yaml
parameters:
  - name: pr_number
    required: true
  - name: depth
    default: shallow
    choices: [shallow, deep]
skills: [code-review]
instructions: |
  You are reviewing PR #{{ pr_number }}.
  Use {{ depth }} mode: shallow = surface issues only;
  deep = trace data flow across files.
prompt: |
  Review PR #{{ pr_number }} on this repo. Focus on correctness bugs.
permission_mode: workspace-write
max_iterations: 30
```

Invocation:

```bash
agentao run --spec .agentao/runs/review-pr.yaml \
            --param pr_number=142 --param depth=deep
```

## Test plan

New tests under `tests/cli/test_run_parameters.py`:

1. **Valid render**: spec with one required param, prompt template uses it →
   `Agentao` receives the rendered prompt.
2. **Required missing**: spec marks param required, no `--param` supplied →
   exit 2, `invalid_spec`, message names the param.
3. **Default applied**: optional param with `default`, no `--param` →
   renders with default.
4. **Choices enforced**: param with `choices=[shallow,deep]`, `--param x=other`
   → exit 2, `invalid_spec`, message lists allowed values.
5. **Unknown param (parameters declared)**: spec declares `[{name: a}]`,
   `--param b=1` supplied → exit 2.
6. **Unknown param (no parameters block)**: spec has no `parameters`,
   `--param x=1` supplied → exit 2 (covers trigger-rule row 2).
7. **No params + no CLI params**: spec has no `parameters`, prompt contains
   literal `{{ literal }}` → passes through unrendered (covers row 1).
8. **StrictUndefined**: prompt references `{{ missing }}` not declared in
   `parameters` → exit 2 with the variable name.
9. **Duplicate parameter name in spec**: spec has two `parameters[*].name:
   depth` → `_parse_spec_text` raises, exit 2, message names the duplicate.
10. **`--param` malformed**: `--param foo` (no `=`) → exit 2 with
    "expected KEY=VALUE".
11. **`--param` duplicate key**: `--param x=1 --param x=2` → exit 2 with
    "supplied multiple times".
12. **`instructions` flows to `project_instructions`**: assert
    `factory_kwargs` passed to `build_from_environment` contains
    `project_instructions=<rendered string>`.
13. **`required` + `default` mutually exclusive**: spec parameter sets
    both → exit 2 at spec-parse time, message names the param.
14. **`default` not in `choices`**: spec parameter has `choices: [a, b]`
    and `default: c` → exit 2 with both values in the message.
15. **Non-identifier parameter name**: parametrize over `""`, `" x "`,
    `"pr-number"`, `"1foo"`, `"foo bar"` — all spec-side, all exit 2 at
    spec-parse time. One subtest also verifies `--param foo-bar=v` fails
    at the CLI parse stage with the identifier-rule message.

Fifteen tests. No mocking of Jinja2 imports, no sys.modules snapshots.

## Out of scope (explicit)

- **`title` / `description` on `RunSpec`**: speculative until `--list` is
  designed. Add together when needed.
- **`number` and `boolean` parameter types**: defer until coercion rules
  have a real consumer driving the spec.
- **`response.json_schema` / FinalOutputTool gating**: separate design
  once consumers exist. Decoupled cleanly from recipes.
- **Recipe `activities` UI pills**: agentao has no desktop UI.
- **Recipe `sub_recipes`**: defer until subagents land
  ([[project_async_tool_landed]] gives us the dispatch primitives but no
  agent-spawning surface yet).
- **AGENTAO.md "both present" warning**: not worth the extra disk probe.
- **Templating fields other than `prompt` and `instructions`**: templating
  `permissions` or `skills` invites footguns.
- **`{% include %}` / file-include in templates**: filesystem-resolution
  scope ambiguity. Defer.
- **Promoting templating into the interactive CLI**: no demand.

## Migration / compatibility

Purely additive. Existing specs validate unchanged because the two new
fields are optional. `extra="forbid"` remains intact.

No deprecations. No callback signature changes. No host-API impact —
`Agentao(project_instructions=...)` already exists and stays untouched.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Silent variable typos | `StrictUndefined` raises; the error message names the variable. |
| Param values containing Jinja syntax | Values are passed as **render context**, never as template source. They are not re-rendered. |
| Param values from untrusted callers | `choices` enforcement is the only hard gate; otherwise the caller's data flows into the prompt — same trust model as `--prompt` today. |
| `{{ }}` literals in specs that don't declare parameters | Renderer no-ops when both spec and CLI params are empty. |
| `--param` typos silently changing behavior | Duplicate-key and unknown-key both error; missing `=` errors. No silent fallbacks. |

## Open questions

1. Should `--param @file.json` load params in bulk for CI matrices? Reasonable
   follow-up; not blocking v1.
2. When does `number` / `boolean` typing land? Recommend "when a consumer
   files an issue with a concrete coercion-rule preference."

## Effort estimate

- Pydantic models + 4 validators + Jinja renderer + `--param` parser +
  wiring line: ~110 LoC
- Tests (15 cases above): ~220 LoC
- Docstrings + a section in `docs/reference/configuration.md`: ~30 lines
- Total: ~360 lines, no core agent changes.

One PR, gated behind the test plan. No host-API surface change → safe to
land in a patch release.

## Implementation notes (shipped 2026-05-25)

The PR matched the design with five additional hardenings surfaced during
in-tree review (`/code-review` + 5 iterations of `/codex:review`). Each delta
below cites the line/file in the shipped tree and the concrete failure mode
that drove it.

### Sandboxed Jinja environment

`run_template.py::_build_environment` uses `jinja2.sandbox.SandboxedEnvironment`,
not the plain `Environment` the draft pseudocode implied. Without the sandbox,
a shared/untrusted recipe could reach Python internals through Jinja globals
such as `cycler` and execute arbitrary code at render time — before
`permission_mode` and tool permissions are applied. `SecurityError` is wrapped
in `RunTemplateError` so a sandbox refusal exits 2 / `invalid_spec` with a
diagnostic message instead of crashing the CLI. Test:
`tests/cli/test_run_parameters.py::test_sandbox_blocks_attribute_escape`.

### Runtime template errors are caught

`template.render()` can propagate arbitrary Python exceptions from inside the
template (`{{ 1/0 }}` → `ZeroDivisionError`, `{{ "x" + 1 }}` → `TypeError`,
`{% include %}` without a loader → `TemplateNotFound`). The renderer wraps
these in `RunTemplateError` via a final `except Exception` (deliberately not
`BaseException`, so `KeyboardInterrupt` / `SystemExit` keep their normal
semantics). Without this branch, those inputs crash the CLI with a Python
traceback instead of the documented exit 2 envelope. Tests:
`test_runtime_template_errors_map_to_invalid_spec` (parametrized over the
three failure modes).

### Jinja-reserved names rejected at spec-validation time

`RunParameter` name validation rejects an explicit blocklist of Jinja
constants (`true`/`True`/`false`/`False`/`none`/`None`) and keywords
(`for`/`if`/`in`/`set`/...) in addition to the ASCII-identifier regex. Both
classes pass `[A-Za-z_][A-Za-z0-9_]*` but break templating: constants
silently win over context variables (`{{ true }}` resolves to Jinja's `True`,
not the supplied value), and keywords produce template syntax errors when
referenced in `{{ }}`. The blocklist also includes `self` and `parent`,
which Jinja's runtime injects unconditionally — accepting them would
silently discard the user's value. Tests:
`test_jinja_reserved_parameter_name_rejected` (parametrized over
constants + keywords + runtime-context names).

### Render context passed positionally

`render_spec` calls `template.render(context)` with a positional dict, not
`template.render(**context)`. The kwargs form collides with `Template.render`'s
bound-method `self` parameter (`got multiple values for argument 'self'`)
when a spec declares a parameter named `self`. The blocklist above already
rejects that name, but passing positionally is defense-in-depth against
any future name we forget to add.

### Whitespace-only rendered instructions fall through to AGENTAO.md

`run.py` guards the `project_instructions` plumbing with
`if spec.instructions and spec.instructions.strip():`, not just truthiness.
A YAML block scalar `instructions: |\n  {{ extra }}\n` renders to `"\n"`
when `extra` is empty (Jinja's `keep_trailing_newline=True` preserves the
trailing newline) — that bare newline is truthy, so the earlier
empty-string guard would have let it through and silently suppressed the
AGENTAO.md fallback. Tests: the original
`test_empty_rendered_instructions_does_not_override_agentao_md` plus
`test_whitespace_only_rendered_instructions_does_not_override_agentao_md`.

### Diagnostic improvements

Two adjacent UX issues surfaced in review and were folded in:

- A template that resolves to an empty prompt now emits "prompt template
  rendered to empty; check --param values" instead of the generic
  "prompt is required" — the latter misled callers about the root cause.
  `_execute_with_args` snapshots pre-render truthiness to distinguish the
  two failure modes.
- Multiple unknown `--param` keys are reported in one error rather than
  one-at-a-time. CLI insertion order is preserved (was `sorted()`), so
  the message points at the keys in the order the user typed them.
- `_render_field` uses a regex (`'([^']+)'`) to extract the offending name
  from `UndefinedError`'s message, instead of `split(" ", 1)[0].strip(...)`.
  The regex handles compound shapes like `'dict object' has no attribute 'missing'`
  cleanly and falls back to the raw message when Jinja's format changes.

### Files touched

- `agentao/cli/run_models.py` — `RunParameter`, `_JINJA_RESERVED_NAMES`,
  validators (identifier + reserved + required-XOR-default + default-in-choices
  + no-duplicate-names).
- `agentao/cli/run_template.py` — `SandboxedEnvironment`, `RunTemplateError`,
  `render_spec`, `_validate_params`, `_render_field`.
- `agentao/cli/run.py` — `--param` flag, `_parse_cli_params`,
  `render_spec` call site, `project_instructions` plumbing (with strip-guard).
- `tests/cli/test_run_parameters.py` — 30 test cases (the 15 from the
  original test plan plus 15 added during review iterations).
- `pyproject.toml` — `jinja2>=3.0.0` as a base dependency.

Final test count: `tests/` was 2666 → 2684 passing (+18 added; +0 regressions).
