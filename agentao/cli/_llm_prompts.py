"""The LLM settings questions shared by ``agentao init`` and ``agentao --login``.

Import-light on purpose: ``--login`` must run from a bare ``pip install
agentao`` (no ``[cli]`` extras), because an ACP Registry client launches
Agentao through ``uvx agentao@<version>``. With ``rich`` installed the prompts
look as they always did; without it they fall back to ``_plain_tty``.
"""

from __future__ import annotations

try:
    from rich.prompt import Prompt

    from ._globals import console
except ImportError:  # bare install: no [cli] extras
    from ._plain_tty import Prompt, console  # type: ignore[assignment]


_PROVIDER_DEFAULTS = {
    "OPENAI":     {"base_url": "https://api.openai.com/v1",                                          "model": "gpt-5.4"},
    "DEEPSEEK":   {"base_url": "https://api.deepseek.com/v1",                                        "model": "deepseek-chat"},
    "GEMINI":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",             "model": "gemini-flash-latest"},
    "ANTHROPIC":  {"base_url": "https://api.anthropic.com/v1",                                       "model": "claude-sonnet-4-6"},
}


def _prompt_llm_settings(*, hide_key: bool = False) -> tuple[str, str, str, str]:
    """Ask for provider, API key, base URL and model; shared by ``init`` and ``--login``.

    Returns ``(provider, api_key, base_url, model)`` with the provider
    upper-cased. ``hide_key`` masks the key as it is typed. Raises
    ``EOFError`` / ``KeyboardInterrupt`` when the user ends input — callers
    decide what a cancel means.
    """
    provider_choices = list(_PROVIDER_DEFAULTS.keys()) + ["CUSTOM"]
    console.print("[bold]Step 1 of 3 — LLM Provider[/bold]")
    for i, name in enumerate(provider_choices, 1):
        console.print(f"  [cyan]{i}[/cyan]  {name}")
    console.print()

    while True:
        raw = Prompt.ask(
            "Choose provider",
            default="1",
        ).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(provider_choices):
            provider = provider_choices[int(raw) - 1]
            break
        upper = raw.upper()
        if upper in provider_choices:
            provider = upper
            break
        console.print("[error]Invalid choice — enter a number or provider name.[/error]")

    while provider == "CUSTOM" or not provider:
        provider = Prompt.ask("Custom provider name (used as env var prefix, e.g. MYAPI)").strip().upper()

    defaults = _PROVIDER_DEFAULTS.get(provider, {"base_url": "", "model": ""})
    console.print()

    console.print("[bold]Step 2 of 3 — API Key[/bold]")
    while True:
        api_key = Prompt.ask(f"{provider}_API_KEY", password=hide_key).strip()
        if api_key:
            break
        console.print("[error]API key is required.[/error]")
    console.print()

    console.print("[bold]Step 3 of 3 — Endpoint & Model[/bold]  [dim](press Enter to accept defaults)[/dim]")
    default_url = defaults["base_url"]
    default_model = defaults["model"]

    while True:
        # ``rich`` answers an empty reply with the default itself, ``None``
        # here (a CUSTOM provider has no default URL or model).
        base_url = (Prompt.ask(
            f"{provider}_BASE_URL",
            default=default_url if default_url else None,
        ) or "").strip()
        if base_url:
            break
        console.print("[error]Base URL is required.[/error]")

    while True:
        # ``rich`` answers an empty reply with the default itself, ``None``
        # here (a CUSTOM provider has no default URL or model).
        model = (Prompt.ask(
            f"{provider}_MODEL",
            default=default_model if default_model else None,
        ) or "").strip()
        if model:
            break
        console.print("[error]Model name is required.[/error]")
    console.print()
    return provider, api_key, base_url, model
