"""``agentao --login`` — the ACP Terminal Auth login process.

An ACP client that supports Terminal Auth launches this as a separate,
interactive process — appending ``--login`` to its usual launch args (the
ACP spec) or replacing them with it (the Registry's description) — and reads
only the exit status: ``0`` is success, anything else is failure. The ACP
server then sees the new configuration on the next ``session/new``.

It asks the same questions as ``agentao init``, plus the endpoint's wire
protocol (``api_format``), but writes the user-level
``~/.agentao/llm.json`` (mode ``0600``) instead of a ``.env`` in the current
directory: the login process and the IDE's later sessions need not share a
working directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from agentao.embedding.llm_config import (
    LLMConfigError,
    load_user_llm_config,
    save_user_llm_config,
    user_llm_config_path,
)

from ._globals import console

#: Providers whose own API is not Chat Completions; every other provider
#: defaults to the OpenAI-compatible wire.
_NATIVE_API_FORMAT = {"ANTHROPIC": "anthropic-messages"}

#: Exit status for a login the user interrupted (Ctrl+C), as a shell reports it.
EXIT_CANCELLED = 130
EXIT_FAILED = 1


def _is_complete(config: Optional[Dict[str, str]]) -> bool:
    return bool(config) and all(config.get(k) for k in ("api_key", "base_url", "model"))


def _prompt_api_format(provider: str, previous: Optional[Dict[str, str]]) -> str:
    """Ask which wire protocol the endpoint speaks.

    Always asked and always saved: an unset format means Chat Completions,
    which is wrong for a native Anthropic endpoint and would only surface as a
    failed request after a "successful" login. The default is the format the
    replaced configuration named for this provider, else the provider's own.
    """
    from agentao.llm._api_format import API_FORMATS, DEFAULT_API_FORMAT

    default = _NATIVE_API_FORMAT.get(provider, DEFAULT_API_FORMAT)
    if previous and previous.get("provider", "").upper() == provider:
        default = previous.get("api_format", default)
    if default not in API_FORMATS:
        default = DEFAULT_API_FORMAT
    console.print("[bold]Wire protocol[/bold]  [dim](press Enter to accept the default)[/dim]")
    fmt = Prompt.ask(f"{provider}_API_FORMAT", choices=list(API_FORMATS), default=default)
    console.print()
    return fmt


def _load_existing(path: Path) -> Optional[Dict[str, str]]:
    try:
        return load_user_llm_config(path)
    except LLMConfigError as exc:
        console.print(f"[warning]The existing configuration is unusable: {exc}[/warning]")
        return None


def _keep_existing(path: Path, existing: Optional[Dict[str, str]]) -> Optional[int]:
    """Offer to keep an existing file. ``None`` means "go on and replace it"."""
    if existing is not None:
        provider = existing.get("provider", "OPENAI")
        console.print(
            f"[warning]A configuration already exists at {path} "
            f"(provider {provider}).[/warning]"
        )
    if Confirm.ask("Replace it?", default=False):
        console.print()
        return None
    if _is_complete(existing):
        console.print("[dim]Kept the existing configuration.[/dim]")
        return 0
    # Declining leaves nothing usable behind; a client reading exit 0 would
    # reconnect into the same auth_required it launched this login to fix.
    console.print("[error]No changes made, and the existing configuration is incomplete.[/error]")
    return EXIT_FAILED


def run_login(config_path: Optional[Path] = None) -> int:
    """Run the login flow. Returns the process exit status.

    ``0`` only when a complete configuration is on disk afterwards — newly
    saved, or an existing complete one the user chose to keep. A cancel
    (Ctrl+C, end of input), a failed save, or declining to replace an
    incomplete file is non-zero, with a one-line message and no traceback.
    """
    from .entrypoints import _prompt_llm_settings

    path = config_path or user_llm_config_path()
    try:
        console.print()
        console.print(Panel.fit(
            "[bold cyan]Agentao[/bold cyan] — LLM provider login\n"
            f"[dim]Saved to {path}; used by Agentao's ACP sessions.[/dim]",
            border_style="cyan",
        ))
        console.print()
        existing: Optional[Dict[str, str]] = None
        if path.exists():
            existing = _load_existing(path)
            kept = _keep_existing(path, existing)
            if kept is not None:
                return kept
        provider, api_key, base_url, model = _prompt_llm_settings(hide_key=True)
        api_format = _prompt_api_format(provider, existing)
    except KeyboardInterrupt:
        console.print("\n[error]Login cancelled. No changes made.[/error]")
        return EXIT_CANCELLED
    except EOFError:
        console.print("\n[error]Login cancelled (end of input). No changes made.[/error]")
        return EXIT_FAILED

    try:
        save_user_llm_config(
            path, provider=provider, api_key=api_key, base_url=base_url, model=model,
            api_format=api_format,
        )
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        console.print(f"[error]Could not save {path}: {reason}[/error]")
        return EXIT_FAILED

    console.print(
        f"[success]Saved.[/success] [dim]{provider} configuration written to {path}. "
        "Reconnect your ACP client to use it.[/dim]"
    )
    return 0
