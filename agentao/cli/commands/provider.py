"""``/provider``, ``/model``, ``/temperature``, ``/thinking`` — LLM config.

All four target the same component (the live ``LLMClient`` underneath
``cli.agent``). ``/provider`` / ``/model`` reuse ``_list_providers_from_env``
to enumerate available ``XXXX_API_KEY`` triples from the environment;
``/temperature`` and ``/thinking`` mutate request fields on the live client
(``temperature`` / ``omit_temperature`` and the ``extra_body`` passthrough).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Mapping

from rich.markup import escape

from ...llm.thinking import (
    REASONING_LEVELS as _REASONING_LEVELS,
    anthropic_effort_levels as _effort_levels,
    ThinkingUnsupported,
    clear_thinking,
    current_thinking,
    set_thinking,
    writable_extra_body,
)
from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def _list_providers_from_env() -> list:
    """Return sorted list of provider names that have API key, base URL, and model in environment."""
    providers = []
    for key, value in os.environ.items():
        if key.endswith("_API_KEY") and value:
            provider = key[: -len("_API_KEY")]
            if os.getenv(f"{provider}_BASE_URL") and os.getenv(f"{provider}_MODEL"):
                providers.append(provider)
    return sorted(providers)


def handle_provider_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /provider command."""
    args = args.strip().upper()

    if not args:
        current_model = cli.agent.get_current_model()
        console.print(f"\n[info]Current Provider:[/info] [cyan]{cli.current_provider}[/cyan]  "
                      f"[dim](model: {current_model})[/dim]\n")

        providers = _list_providers_from_env()
        if not providers:
            console.print("[warning]No providers found in .env (looking for XXXX_API_KEY entries)[/warning]\n")
            return

        console.print("[info]Available Providers:[/info]")
        for p in providers:
            marker = " [green]✓[/green]" if p == cli.current_provider else ""
            console.print(f"  • {p}{marker}")
        console.print("\n[info]Usage:[/info] /provider <NAME>  (e.g. /provider GEMINI)\n")

    else:
        api_key = os.getenv(f"{args}_API_KEY")
        if not api_key:
            console.print(f"\n[error]No API key found for provider '{args}' "
                           f"(expected env var: {args}_API_KEY)[/error]\n")
            return

        base_url = os.getenv(f"{args}_BASE_URL") or None
        if not base_url:
            console.print(f"\n[error]No base URL configured for provider '{args}' "
                           f"(expected env var: {args}_BASE_URL, "
                           f"e.g. {args}_BASE_URL=https://api.openai.com/v1)[/error]\n")
            return

        model = os.getenv(f"{args}_MODEL") or None
        if not model:
            console.print(f"\n[error]No model configured for provider '{args}' "
                           f"(expected env var: {args}_MODEL, e.g. {args}_MODEL=gpt-5.4)[/error]\n")
            return

        # The block names its own wire protocol, and the switch carries it:
        # without it the new credentials and base URL would go to the previous
        # protocol's SDK. Resolved before anything is touched, so a bad value
        # leaves the session on the provider it had.
        from ...llm._api_format import DEFAULT_API_FORMAT, resolve_api_format

        try:
            target_format = resolve_api_format(os.getenv(f"{args}_API_FORMAT"))
        except ValueError as exc:
            console.print(f"\n[error]{escape(str(exc))} "
                          f"(env var: {args}_API_FORMAT)[/error]\n")
            return
        live_format = getattr(cli.agent.llm, "api_format", None) or DEFAULT_API_FORMAT
        cli.agent.set_provider(
            api_key=api_key, base_url=base_url, model=model,
            api_format=target_format,
        )
        cli.current_provider = args

        current_model = cli.agent.get_current_model()
        console.print(f"\n[success]Switched to provider: {args}[/success]")
        console.print(f"[info]Model:[/info] [cyan]{current_model}[/cyan]")
        if target_format != live_format:
            console.print(f"[info]Wire protocol:[/info] {live_format} → "
                          f"[cyan]{target_format}[/cyan]")
            stale = sorted(getattr(cli.agent.llm, "extra_body", None) or {})
            if stale:
                console.print(
                    f"[warning]extra_body ({escape(', '.join(stale))}) was "
                    f"configured for {live_format} and is sent to "
                    f"{target_format} unchanged.[/warning]"
                )
        console.print()


def _handle_thinking_anthropic(llm: object, args: str) -> None:
    """``/thinking`` on the ``anthropic-messages`` wire: ``output_config.effort``.

    ``reasoning_effort`` is a Chat Completions field and the Messages API
    rejects it, so here a level is written to ``output_config.effort`` instead
    — which on current models also turns adaptive thinking on (observed). The
    level is checked against the Models API's ``capabilities.effort`` when the
    endpoint has told us, else against the five values the API accepts; there
    is still no auto-recovery, so an unlisted word is refused rather than
    stored. Other ``output_config`` keys a host set are left alone.
    """
    is_set, current = current_thinking(llm)
    levels = _effort_levels(getattr(llm, "model_capabilities", None))
    usage = f"[dim]Usage: /thinking <{' | '.join(levels)} | off>[/dim]\n"

    if not args:
        shown = escape(str(current)) if is_set else "default"
        console.print(f"\n[info]Thinking depth:[/info] [cyan]{shown}[/cyan] "
                      "[dim](output_config.effort)[/dim]")
        console.print(usage)
        return

    lowered = args.lower()
    if lowered == "off":
        # A ``reasoning_effort`` carried over a wire switch fails every request
        # here, so ``off`` clears that too.
        if not clear_thinking(llm):
            console.print("\n[info]Thinking depth already at provider default "
                          "(output_config.effort unset).[/info]\n")
            return
        console.print("\n[success]Thinking depth off — output_config.effort "
                      "cleared; provider default in effect.[/success]\n")
        return

    try:
        # The one check of the level: ``set_thinking`` refuses one the model
        # does not list, against the same ``levels`` shown in ``usage``.
        # ``handle_thinking_command`` has made sure there is a dict to write.
        set_thinking(llm, lowered)
    except ThinkingUnsupported as exc:
        # Not a fact about the level: do not call it an invalid depth.
        console.print(f"\n[error]Cannot set reasoning effort: {escape(str(exc))}.[/error]\n")
        return
    except ValueError:
        console.print(f"\n[error]Invalid thinking depth for this model: "
                      f"'{escape(args)}'.[/error]")
        console.print(usage)
        return
    if not is_set:
        console.print(f"\n[success]Thinking depth set to [cyan]{lowered}[/cyan]"
                      "[/success] [dim](output_config.effort)[/dim]")
    else:
        console.print(f"\n[success]Thinking depth changed from {escape(str(current))} "
                      f"to [cyan]{lowered}[/cyan][/success]")
    console.print("[dim]No auto-recovery: if this model rejects output_config.effort, "
                  "requests fail until /thinking off.[/dim]\n")


def handle_model_command(cli: AgentaoCLI, args: str) -> None:
    """Handle model command."""
    args = args.strip()

    if not args:
        current = cli.agent.get_current_model()
        console.print(f"\n[info]Current Model:[/info] [cyan]{current}[/cyan]\n")
        try:
            with console.status("[dim]Fetching available models…[/dim]"):
                available = cli.agent.list_available_models()
        except RuntimeError as e:
            console.print(f"[error]Failed to list models: {e}[/error]\n")
            return

        console.print("[info]Available Models:[/info]\n")

        claude_models = [m for m in available if m.startswith("claude-")]
        gpt_models = [m for m in available if m.startswith("gpt-")]
        other_models = [m for m in available if not m.startswith(("claude-", "gpt-"))]

        if claude_models:
            console.print("  [bold]Claude:[/bold]")
            for model in claude_models:
                marker = " [green]✓[/green]" if model == current else ""
                console.print(f"    • {model}{marker}")

        if gpt_models:
            console.print("\n  [bold]OpenAI GPT:[/bold]")
            for model in gpt_models:
                marker = " [green]✓[/green]" if model == current else ""
                console.print(f"    • {model}{marker}")

        if other_models:
            console.print("\n  [bold]Other:[/bold]")
            for model in other_models:
                marker = " [green]✓[/green]" if model == current else ""
                console.print(f"    • {model}{marker}")

        console.print("\n[info]Usage:[/info] /model <model_name>")
        console.print("Example: /model claude-sonnet-4-6\n")

    else:
        result = cli.agent.set_model(args)
        console.print(f"\n[success]{result}[/success]\n")


#: Why ``/temperature`` reports rather than acts on ``anthropic-messages``.
_TEMPERATURE_UNSENT = (
    "the anthropic-messages wire has no 'temperature' parameter; "
    "a gateway that takes one gets it through LLM_EXTRA_BODY"
)


#: How ``/temperature`` names an unset value — nothing is sent.
_UNSET_LABEL = "provider default"


def handle_temperature_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /temperature command — show or set LLM temperature.

    The ``anthropic-messages`` wire never sends ``temperature`` (see
    ``llm/_anthropic_messages.py``). The value is still stored there — it is
    the client's, and a ``/provider`` switch back to Chat Completions sends it
    — but every answer says it is not going out, instead of "sending 0.7".

    No value (``None``, the default) means nothing is sent and the provider's
    own default applies; ``on`` cannot turn that into a value, so it says so.
    """
    args = args.strip()
    unsent = getattr(cli.agent.llm, "api_format", None) == "anthropic-messages"
    if not args:
        if cli.agent.llm.temperature is None:
            console.print(
                f"\n[info]Temperature:[/info] [cyan]{_UNSET_LABEL}[/cyan] "
                "[dim](not sent)[/dim]"
            )
        elif unsent:
            console.print(
                f"\n[info]Temperature:[/info] [cyan]{cli.agent.llm.temperature}[/cyan] "
                f"[warning](not sent — {_TEMPERATURE_UNSENT})[/warning]"
            )
        elif getattr(cli.agent.llm, "omit_temperature", False):
            console.print("\n[info]Temperature:[/info] [cyan]off[/cyan] [dim](omitted from requests)[/dim]")
        else:
            console.print(f"\n[info]Temperature:[/info] [cyan]{cli.agent.llm.temperature}[/cyan]")
        console.print("[dim]Usage: /temperature <value>  (0.0 - 2.0) | off | on[/dim]\n")
        return

    lowered = args.lower()
    if lowered == "off":
        cli.agent.llm.omit_temperature = True
        console.print("\n[success]Temperature off — 'temperature' will be omitted from requests[/success]\n")
        return
    if lowered == "on":
        cli.agent.llm.omit_temperature = False
        if cli.agent.llm.temperature is None:
            console.print(
                f"\n[warning]No temperature is set ({_UNSET_LABEL}), so nothing is "
                "sent — use /temperature <value> to send one[/warning]\n"
            )
            return
        if unsent:
            console.print(
                f"\n[warning]Temperature on ({cli.agent.llm.temperature}), but not sent — "
                f"{_TEMPERATURE_UNSENT}[/warning]\n"
            )
            return
        console.print(f"\n[success]Temperature on — sending {cli.agent.llm.temperature}[/success]\n")
        return

    try:
        value = float(args)
    except ValueError:
        console.print(f"\n[error]Invalid temperature value: {args}[/error]\n")
        return
    if not 0.0 <= value <= 2.0:
        console.print("\n[error]Temperature must be between 0.0 and 2.0[/error]\n")
        return
    old = cli.agent.llm.temperature
    if old is None:
        old = _UNSET_LABEL
    cli.agent.llm.temperature = value
    cli.agent.llm.omit_temperature = False
    if unsent:
        console.print(
            f"\n[warning]Temperature stored as {value} (was {old}), but not sent — "
            f"{_TEMPERATURE_UNSENT}[/warning]\n"
        )
        return
    console.print(f"\n[success]Temperature changed from {old} to {value}[/success]\n")


def _handle_thinking_responses(llm: object, extra_body: Mapping, args: str) -> None:
    """``/thinking`` on the ``openai-responses`` wire: ``reasoning.effort``.

    The Responses API spells the field as a key of a ``reasoning`` object and
    answers a top-level ``reasoning_effort`` with a 400 — on every later
    request, since the value is stored. So a level is written to
    ``reasoning.effort``, and a ``reasoning_effort`` carried in across a
    provider switch is dropped by the same command that replaces it. Other
    ``reasoning`` keys a host set (``summary``) are left alone. The value is
    not validated, as on Chat Completions: the scale is the model's
    (``none`` and ``xhigh`` exist on some), and the provider checks it.
    """
    # A ``reasoning`` that is not an object is not something this API
    # accepts, and every request would say so: it reads as the level it was
    # presumably meant to be, so ``off`` and a new level both replace it.
    is_set, current = current_thinking(llm)
    malformed = "reasoning" in extra_body and not isinstance(extra_body["reasoning"], dict)
    stale = "reasoning_effort" in extra_body
    usage = f"[dim]Usage: /thinking <{' | '.join(_REASONING_LEVELS)} | off>[/dim]\n"

    if not args:
        shown = escape(str(current)) if is_set else "default"
        console.print(f"\n[info]Thinking depth:[/info] [cyan]{shown}[/cyan] "
                      "[dim](reasoning.effort)[/dim]")
        # ``/thinking`` writes only into a dict: a read-only mapping must be
        # changed by the host, so do not advise a command that refuses it.
        writable = isinstance(extra_body, dict)
        if malformed:
            console.print(
                "[warning]extra_body's `reasoning` is not an object, which this "
                "wire rejects on every request; "
                + ("/thinking off or a new level replaces it." if writable
                   else "the host must replace it (extra_body is read-only here).")
                + "[/warning]")
        if stale:
            console.print(
                "[warning]extra_body also carries `reasoning_effort` "
                f"('{escape(str(extra_body['reasoning_effort']))}'), which this "
                "wire rejects; "
                + ("/thinking off or a new level clears it." if writable
                   else "the host must remove it (extra_body is read-only here).")
                + "[/warning]")
        console.print(usage)
        return

    lowered = args.lower()
    if lowered == "off":
        if not clear_thinking(llm):
            console.print("\n[info]Thinking depth already at provider default "
                          "(reasoning.effort unset).[/info]\n")
            return
        console.print("\n[success]Thinking depth off — reasoning.effort "
                      "cleared; provider default in effect.[/success]\n")
        return

    if len(args.split()) > 1:
        console.print(f"\n[error]Invalid thinking depth: '{escape(args)}' — expected a "
                      f"single level ({' | '.join(_REASONING_LEVELS)}) or 'off', "
                      "not multiple words.[/error]\n")
        return

    value = set_thinking(llm, args)
    note = ""
    if value not in _REASONING_LEVELS:
        note = (f"  [dim](non-standard — your provider validates it; "
                f"standard: {', '.join(_REASONING_LEVELS)})[/dim]")
    if not is_set:
        console.print(f"\n[success]Thinking depth set to [cyan]{escape(value)}[/cyan]"
                      f"[/success] [dim](reasoning.effort)[/dim]{note}")
    else:
        console.print(f"\n[success]Thinking depth changed from {escape(str(current))} "
                      f"to [cyan]{escape(value)}[/cyan][/success]{note}")
    console.print("[dim]No auto-recovery: if this model rejects reasoning.effort, "
                  "requests fail until /thinking off.[/dim]\n")


def handle_thinking_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /thinking command — show/set the model's thinking depth.

    "Thinking depth" maps to the ``reasoning_effort`` request field, which the
    harness carries in the live LLM client's ``extra_body`` passthrough (the one
    place provider-specific request fields live — there is no typed kwarg for
    it). Mutating ``extra_body`` here updates the live client *and* every
    sub-agent launched afterward, because ``Agentao._llm_config`` reads
    ``extra_body`` live on each access.

    Unlike ``/temperature`` there is **no auto-recovery latch** (``extra_body``
    has no ``omit_*`` mirror): if the active model rejects ``reasoning_effort``,
    every subsequent call 400s until ``/thinking off`` clears it. That asymmetry
    is documented in ``docs/design/host-llm-extra-params.md`` §"no auto-recovery".

    On the ``anthropic-messages`` wire the field is ``output_config.effort``
    instead; see :func:`_handle_thinking_anthropic`. On ``openai-responses`` it
    is ``reasoning.effort``; see :func:`_handle_thinking_responses`.
    """
    llm = cli.agent.llm
    args = args.strip()
    try:
        # The write rule is ``llm/thinking.py``'s: a client with no
        # ``extra_body`` has nowhere to write, ``None`` becomes a dict, and
        # anything else must be a dict. A bare ``/thinking`` only reads, and
        # ``current_thinking`` reads any mapping, so a read-only one still
        # shows its level. One that is not a mapping at all (a ``str``)
        # would make the readers raise.
        extra_body = getattr(llm, "extra_body", None)
        if args or extra_body is None:
            extra_body = writable_extra_body(llm)
        elif not isinstance(extra_body, Mapping):
            raise ThinkingUnsupported("the LLM client's extra_body is not a dict")
    except ThinkingUnsupported as exc:
        console.print(f"\n[error]Cannot set reasoning effort: {escape(str(exc))}.[/error]\n")
        return

    if getattr(llm, "api_format", None) == "anthropic-messages":
        _handle_thinking_anthropic(llm, args)
        return
    if getattr(llm, "api_format", None) == "openai-responses":
        _handle_thinking_responses(llm, extra_body, args)
        return
    # Membership, not ``.get() is None`` (``current_thinking``): a host may set
    # ``reasoning_effort=None`` explicitly (which is still sent to the
    # provider), and ``off`` must be able to clear *that* too.
    is_set, current = current_thinking(llm)

    if not args:
        if not is_set:
            console.print("\n[info]Thinking depth:[/info] [cyan]default[/cyan] "
                          "[dim](reasoning_effort unset — provider default)[/dim]")
        else:
            console.print(f"\n[info]Thinking depth:[/info] [cyan]{escape(str(current))}[/cyan] "
                          "[dim](reasoning_effort)[/dim]")
        console.print(f"[dim]Usage: /thinking <{' | '.join(_REASONING_LEVELS)} | off>[/dim]\n")
        return

    lowered = args.lower()

    # ``off`` clears the key (use the provider default). It is NOT a passthrough
    # value: a provider whose scale includes a literal "none" effort is set with
    # ``/thinking none`` instead — keeping the disable keyword unambiguous.
    if lowered == "off":
        if not clear_thinking(llm):
            console.print("\n[info]Thinking depth already at provider default "
                          "(reasoning_effort unset).[/info]\n")
            return
        console.print(f"\n[success]Thinking depth off — reasoning_effort "
                      f"('{escape(str(current))}') cleared; provider default in effect.[/success]\n")
        return

    # Reject a multi-word argument: ``/thinking high please`` would otherwise
    # store "high please" verbatim and 400 every later request with no clear
    # cause. A reasoning_effort token is always a single word.
    if len(args.split()) > 1:
        console.print(f"\n[error]Invalid thinking depth: '{escape(args)}' — expected a "
                      f"single level ({' | '.join(_REASONING_LEVELS)}) or 'off', "
                      "not multiple words.[/error]\n")
        return

    # Normalize a *known* level to its canonical lowercase form (so ``HIGH`` →
    # ``high``), but pass a non-standard token through verbatim — forcing
    # lowercase would silently mangle a provider whose scale is case-sensitive.
    value = set_thinking(llm, args)
    note = ""
    if value not in _REASONING_LEVELS:
        note = (f"  [dim](non-standard — your provider validates it; "
                f"standard: {', '.join(_REASONING_LEVELS)})[/dim]")
    safe_value = escape(value)
    if not is_set:
        console.print(f"\n[success]Thinking depth set to [cyan]{safe_value}[/cyan][/success]{note}")
    else:
        console.print(f"\n[success]Thinking depth changed from {escape(str(current))} to "
                      f"[cyan]{safe_value}[/cyan][/success]{note}")
    console.print("[dim]No auto-recovery: if this model rejects reasoning_effort, "
                  "requests fail until /thinking off.[/dim]\n")
