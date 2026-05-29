"""``/provider``, ``/model``, ``/temperature`` — LLM provider config.

All three target the same component (the live ``LLMClient`` underneath
``cli.agent``) and reuse ``_list_providers_from_env`` to enumerate
available ``XXXX_API_KEY`` triples from the environment.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

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
