"""The welcome banner names the build it came from."""

from types import SimpleNamespace

import agentao
from agentao.cli import ui


def test_the_banner_shows_the_installed_version(monkeypatch):
    printed = []
    monkeypatch.setattr(ui.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else ""))
    monkeypatch.setattr(agentao, "__version__", "9.8.7.dev6")

    ui.print_welcome(SimpleNamespace(agent=SimpleNamespace(get_current_model=lambda: "m-1")))

    tagline = [line for line in printed if "The Way of Agents" in line]
    assert len(tagline) == 1 and "v9.8.7.dev6" in tagline[0]
    assert any("m-1" in line for line in printed)
