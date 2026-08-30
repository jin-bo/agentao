"""The three path placeholders — §2.4 and §7.1 of the conformance plan.

`${CLAUDE_PROJECT_DIR}`, `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are
substituted into ``command`` **and each ``args`` element**, and exported as
environment variables on the spawned process. Three, not two: the data directory
is the one that gets forgotten, and it is the one that needs a decision rather
than a substitution — agentao has no per-plugin data directory, so this module
defines one.

**The trap this module exists to keep visible** (§7.1): ``_run_subprocess``
passes no ``env=`` today, which is precisely why the hook child gets
``build_child_env()`` and the harness's provider credentials are stripped — one
of the five places agentao leads both peers. Writing the export as ``env={...}``
or ``env=os.environ | {...}`` deletes that scrub silently. The only correct
spelling is ``env=build_child_env({...})``, which applies overrides *after* the
scrub by construction.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..models import ParsedHookRule


def plugin_data_dir(plugin_name: str | None) -> Path:
    """Where ``${CLAUDE_PLUGIN_DATA}`` points.

    Under the user's agentao home rather than inside the plugin's own tree: a
    plugin directory may be read-only (installed from a marketplace, or checked
    into a repository), and a data directory that cannot be written to is worse
    than none — the hook fails at its first write with an error pointing nowhere
    near the cause.

    Not created here. Creating it on *every* dispatch would make a directory for
    every plugin that never writes anything; the hook creates it if it needs it,
    which is also how it learns the path is writable.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (plugin_name or "unknown"))
    return Path.home() / ".agentao" / "plugin-data" / safe


def _placeholder_values(rule: "ParsedHookRule", cwd: Path | str) -> dict[str, str]:
    """The three placeholder values for one rule.

    ``CLAUDE_PLUGIN_ROOT`` is empty when the rule came from an inline hooks dict
    rather than a plugin directory. Empty rather than absent: a hook that
    substitutes it gets an empty string it can test, instead of a literal
    ``${CLAUDE_PLUGIN_ROOT}`` that a shell would try to run.
    """
    return {
        "CLAUDE_PROJECT_DIR": str(cwd),
        "CLAUDE_PLUGIN_ROOT": rule.plugin_root or "",
        "CLAUDE_PLUGIN_DATA": str(plugin_data_dir(rule.plugin_name)),
    }


def _substitute(text: str, values: dict[str, str]) -> str:
    """Replace ``${NAME}`` for the three known names only.

    Deliberately not ``string.Template`` or ``os.path.expandvars``: both would
    expand *arbitrary* names, so a command containing ``$HOME`` or a literal
    ``${SOMETHING}`` the author meant the shell to see would be rewritten here
    instead — silently, and differently from how the shell would have done it.
    """
    for name, value in values.items():
        text = text.replace("${" + name + "}", value)
    return text
