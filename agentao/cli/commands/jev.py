"""/jev — optional skill recommendation and private credential entry."""
from __future__ import annotations

import getpass
import warnings
from dataclasses import replace

from rich.prompt import Confirm

from ...embedding.jev import load_jev, resolve_typesafe_key, save_jev_settings, save_typesafe_key
from ...recommendations import JevSkillRecommender
from .._globals import console, split_subcommand


def read_hidden_key() -> str:
    """Abort rather than fall back to echoing a secret on a redirected terminal."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass("TypeSafe API key (hidden; Enter to cancel): ").strip()


def _setup(service: JevSkillRecommender) -> bool:
    console.print("Jev sends your request and skill descriptions/excerpts to TypeSafe.")
    try:
        key = read_hidden_key()
        if not key:
            console.print("Setup cancelled; existing key unchanged.")
            return False
        if any(ord(c) < 33 or ord(c) > 126 for c in key):
            console.print("Invalid API key; use printable ASCII without whitespace.")
            return False
        save = Confirm.ask(
            "Save for all projects in ~/.agentao/credentials.json (plaintext)?",
            default=False, console=console,
        )
        if save:
            save_typesafe_key(key)
        service.api_key = key
        console.print("API key configured" + (" and saved for future sessions." if save
                                               else " for this session only."))
        return True
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        console.print("Setup cancelled. You can also set TYPESAFE_API_KEY in your project .env.")
        return False
    except (OSError, ValueError):
        # Never echo exceptions: filesystem/parse failures can contain key data.
        console.print("Could not save the API key; existing configuration unchanged.")
        return False


def handle_jev_command(cli, args: str) -> None:
    sub, rest = split_subcommand(args, default="status", lower=True)
    if rest or sub not in {"on", "off", "status", "setup", "save"}:
        console.print("Usage: /jev on|off|status|setup|save. Enter keys only in the hidden setup prompt.")
        return
    agent = cli.agent
    service = getattr(agent, "skill_recommender", None)
    if service is None:
        service = load_jev(agent.working_directory)
        agent.skill_recommender = service
    if not isinstance(service, JevSkillRecommender):
        console.print("This host provides its own skill recommender; configure it in the host.")
        return
    if sub == "setup":
        _setup(service)
    elif sub == "on":
        if not service.api_key:
            service.api_key = resolve_typesafe_key(agent.working_directory)
        if not service.api_key and not _setup(service):
            return
        service.config = replace(service.config, enabled=True)
        console.print("Jev skill suggestions ON for this session. Requests and skill metadata "
                      "are sent to TypeSafe. Use /jev save to keep this setting.")
    elif sub == "off":
        service.config = replace(service.config, enabled=False)
        agent._skill_suggestion = None
        console.print("Jev skill suggestions OFF for this session. Use /jev save to keep this setting.")
    elif sub == "save":
        try:
            save_jev_settings(agent.working_directory, service.config)
            console.print("Jev settings saved for this project. API key is not included.")
        except (OSError, ValueError):
            console.print("Could not save Jev settings. Check .agentao/settings.json and file permissions.")
    else:
        # No key fragments, payloads or remote diagnostic text, even at debug level.
        console.print(f"Jev: {'ON' if service.config.enabled else 'OFF'}", markup=False)
        console.print(f"API key: {'configured' if service.api_key else 'not configured'}", markup=False)
        console.print(f"Model: {service.config.model}", markup=False)
        console.print(f"Timeout: {service.config.timeout_ms} ms; confidence threshold: "
                      f"{service.config.min_confidence:g}; mode: suggest", markup=False)
        console.print(f"Last result: {service.last_status}", markup=False)
