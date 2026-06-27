"""``/provider``, ``/model``, ``/temperature``, ``/thinking`` — LLM config.

All four target the same component (the live ``LLMClient`` underneath
``cli.agent``). ``/provider`` / ``/model`` reuse ``_list_providers_from_env``
to enumerate available ``XXXX_API_KEY`` triples from the environment;
``/temperature`` and ``/thinking`` mutate request fields on the live client
(``temperature`` / ``omit_temperature`` and the ``extra_body`` passthrough).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from rich.markup import escape

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

        cli.agent.set_provider(api_key=api_key, base_url=base_url, model=model)
        cli.current_provider = args

        current_model = cli.agent.get_current_model()
        console.print(f"\n[success]Switched to provider: {args}[/success]")
        console.print(f"[info]Model:[/info] [cyan]{current_model}[/cyan]\n")


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


def handle_temperature_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /temperature command — show or set LLM temperature."""
    args = args.strip()
    if not args:
        if getattr(cli.agent.llm, "omit_temperature", False):
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
    cli.agent.llm.temperature = value
    cli.agent.llm.omit_temperature = False
    console.print(f"\n[success]Temperature changed from {old} to {value}[/success]\n")


#: Canonical reasoning-effort levels, low → high. Provider-neutral: this is the
#: OpenAI o-series / gpt-5 scale that most OpenAI-compatible providers mirror.
#: The harness deliberately does NOT validate the value — the host configures
#: its own endpoint and the provider validates (see
#: ``docs/design/host-llm-extra-params.md`` §"Validate body values"). These are
#: only the suggested set shown in usage + tab-completion; any other token is
#: passed through with a "non-standard" note.
_REASONING_LEVELS = ("minimal", "low", "medium", "high")


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
    """
    llm = cli.agent.llm
    if not hasattr(llm, "extra_body"):
        # Defensive: a host that injected a non-``LLMClient`` may lack the attr
        # entirely — there is nowhere to carry reasoning_effort.
        console.print("\n[error]Active LLM client has no extra_body passthrough; "
                      "cannot set reasoning effort.[/error]\n")
        return
    if llm.extra_body is None:
        # An injected client may default ``extra_body`` to ``None``; initialize
        # it so the setter is functional rather than silently inert.
        llm.extra_body = {}
    extra_body = llm.extra_body

    args = args.strip()
    # Membership, not ``.get() is None``: a host may set ``reasoning_effort=None``
    # explicitly (which is still sent to the provider), and ``off`` must be able
    # to clear *that* too — conflating the two would make ``off`` a no-op.
    is_set = "reasoning_effort" in extra_body
    current = extra_body.get("reasoning_effort")

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
        if not is_set:
            console.print("\n[info]Thinking depth already at provider default "
                          "(reasoning_effort unset).[/info]\n")
            return
        prev = extra_body.pop("reasoning_effort", None)
        console.print(f"\n[success]Thinking depth off — reasoning_effort "
                      f"('{escape(str(prev))}') cleared; provider default in effect.[/success]\n")
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
    value = lowered if lowered in _REASONING_LEVELS else args
    extra_body["reasoning_effort"] = value
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
