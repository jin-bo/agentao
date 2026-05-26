"""Jinja2 renderer for :class:`RunSpec` templates.

Only ``prompt`` and ``instructions`` are templated. ``--param`` values
flow in as render context — they are never re-rendered as Jinja
sources. ``StrictUndefined`` keeps silent variable typos from
shipping unnoticed.

Failure modes are surfaced as :class:`RunTemplateError` so the caller
in :mod:`agentao.cli.run` can route them through ``_emit_invalid_usage``
(exit 2, ``invalid_spec``) with a uniform message prefix.
"""

from __future__ import annotations

import re
from typing import Dict, List, Mapping, Optional

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

from .run_models import RunParameter, RunSpec


# Matches the leading ``'name'`` token in Jinja's StrictUndefined
# message (``'foo' is undefined``). Falls back to the raw message text
# when the format ever changes so we never emit an empty error.
_UNDEFINED_NAME_RE = re.compile(r"'([^']+)'")


class RunTemplateError(ValueError):
    """Raised by :func:`render_spec` when params or templates are invalid.

    A dedicated subclass keeps the ``except`` clause in
    :mod:`agentao.cli.run` explicit and stops it accidentally swallowing
    unrelated ``ValueError``s from Pydantic.
    """


def _build_environment() -> SandboxedEnvironment:
    # ``SandboxedEnvironment`` is the contract: run specs may come from
    # shared / untrusted recipes, and Jinja's default ``Environment``
    # exposes Python builtins via globals (e.g. ``cycler``) that allow
    # arbitrary code execution at render time — before permission_mode
    # or tool permissions are applied. The sandbox blocks attribute and
    # item access on unsafe builtins.
    # ``autoescape=False`` — output is fed back into prompt text, not
    # HTML. Keeping ``StrictUndefined`` is the contract: a typo in the
    # template should fail loudly at render time, not produce an empty
    # substitution.
    return SandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def _validate_params(
    declared: list[RunParameter],
    supplied: Mapping[str, str],
) -> Dict[str, str]:
    """Merge ``supplied`` over declared defaults; enforce ``required`` / ``choices``."""
    declared_by_name = {p.name: p for p in declared}

    # Preserve the user's CLI order so the error message points at the
    # surplus key they're most likely scanning the command for. Report
    # all unknown keys so users don't have to fix them one at a time.
    unknown: List[str] = [k for k in supplied if k not in declared_by_name]
    if unknown:
        if len(unknown) == 1:
            raise RunTemplateError(
                f"agentao run: unknown parameter {unknown[0]!r}",
            )
        names = ", ".join(repr(k) for k in unknown)
        raise RunTemplateError(
            f"agentao run: unknown parameters {names}",
        )

    merged: Dict[str, str] = {}
    for param in declared:
        if param.name in supplied:
            value = supplied[param.name]
        elif param.default is not None:
            value = param.default
        elif param.required:
            raise RunTemplateError(
                f"agentao run: parameter {param.name!r} is required",
            )
        else:
            # Optional, no default supplied — leave undefined so
            # ``StrictUndefined`` surfaces template typos cleanly.
            continue

        if param.choices is not None and value not in param.choices:
            raise RunTemplateError(
                f"agentao run: parameter {param.name!r} must be one of "
                f"{param.choices}",
            )

        merged[param.name] = value
    return merged


def _render_field(env: SandboxedEnvironment, source: Optional[str], field: str, context: Mapping[str, str]) -> Optional[str]:
    if source is None:
        return None
    try:
        template = env.from_string(source)
    except TemplateSyntaxError as exc:
        raise RunTemplateError(
            f"agentao run: template syntax error in spec.{field}: {exc.message}",
        ) from exc
    try:
        # Pass context as a positional dict, not **kwargs. ``render`` is a
        # bound method, so ``**context`` collides with the implicit ``self``
        # when a parameter is named ``self`` (raises
        # ``got multiple values for argument 'self'``). The positional-dict
        # form is documented and avoids any kwarg name shadowing.
        return template.render(context)
    except SecurityError as exc:
        # Sandbox refused an attribute / item access that would have
        # reached Python internals — surface as invalid_spec so callers
        # can distinguish "your template tried to break out" from a
        # benign undefined-variable typo.
        raise RunTemplateError(
            f"agentao run: template in spec.{field} attempted a "
            f"sandbox-blocked operation: {exc}",
        ) from exc
    except UndefinedError as exc:
        # ``StrictUndefined`` raises ``UndefinedError`` with a message
        # like ``'foo' is undefined``. The regex extracts the first
        # quoted token, which is robust to message variants such as
        # ``'dict object' has no attribute 'missing'`` (where we still
        # want to surface the offending name). If Jinja ever drops the
        # quoting convention entirely, fall back to the raw message
        # so the user still sees something useful.
        raw = str(exc)
        match = _UNDEFINED_NAME_RE.search(raw)
        token = match.group(1) if match else raw
        raise RunTemplateError(
            f"agentao run: template uses undefined variable {token!r} "
            "(declare it in spec.parameters)",
        ) from exc
    except Exception as exc:
        # Catch-all for everything else a template can raise at render
        # time: ``ZeroDivisionError`` from ``{{ 1 / 0 }}``, ``TypeError``
        # from ``{{ "x" + 1 }}``, ``TemplateNotFound`` from a stray
        # ``{% include %}``, etc. Without this branch those crash the
        # CLI with a Python traceback instead of returning the
        # documented exit 2 / ``invalid_spec`` envelope. We deliberately
        # do NOT widen to ``BaseException`` so KeyboardInterrupt /
        # SystemExit keep their normal semantics.
        raise RunTemplateError(
            f"agentao run: template error in spec.{field}: {exc}",
        ) from exc


def render_spec(spec: RunSpec, params: Mapping[str, str]) -> RunSpec:
    """Validate ``params`` and render ``spec.prompt`` + ``spec.instructions``.

    Trigger rule (see design doc): the renderer is invoked by the caller
    only when ``spec.parameters`` is non-empty *or* ``params`` is
    non-empty. The "no parameters declared, no params supplied" path
    is a literal pass-through (Jinja2 is not invoked at all), so a
    parameterless spec can carry literal ``{{ }}`` in its prompt
    without surprise rendering.
    """
    declared = list(spec.parameters or [])
    supplied = dict(params)

    if not declared and supplied:
        # Preserve the user's CLI order in the error so they can scan
        # their command line front-to-back. Aggregate all keys so a
        # multi-typo run yields one error, not N round-trips.
        keys = list(supplied)
        if len(keys) == 1:
            raise RunTemplateError(
                f"agentao run: unknown parameter {keys[0]!r}",
            )
        names = ", ".join(repr(k) for k in keys)
        raise RunTemplateError(
            f"agentao run: unknown parameters {names}",
        )

    if not declared and not supplied:
        return spec

    context = _validate_params(declared, supplied)

    env = _build_environment()
    rendered_prompt = _render_field(env, spec.prompt, "prompt", context)
    rendered_instructions = _render_field(env, spec.instructions, "instructions", context)

    # ``model_copy`` preserves all other fields verbatim and keeps the
    # original ``parameters`` list — callers may still inspect them.
    return spec.model_copy(update={
        "prompt": rendered_prompt,
        "instructions": rendered_instructions,
    })


__all__ = ["RunTemplateError", "render_spec"]
