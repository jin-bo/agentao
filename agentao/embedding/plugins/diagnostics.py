"""Plugin diagnostics snapshot for CLI, logging, and testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from agentao.plugins.models import LoadedPlugin, PluginLoadError, PluginWarning


@dataclass
class PluginDiagnostics:
    """Immutable snapshot of plugin loading results."""

    loaded: list[LoadedPlugin] = field(default_factory=list)
    warnings: list[PluginWarning] = field(default_factory=list)
    errors: list[PluginLoadError] = field(default_factory=list)

    @property
    def plugin_count(self) -> int:
        return len(self.loaded)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def summary(self) -> str:
        """One-line summary for CLI status display."""
        parts: list[str] = [f"{self.plugin_count} plugin(s) loaded"]
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning(s)")
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        return ", ".join(parts)

    def format_report(self) -> str:
        """Multi-line diagnostic report."""
        lines: list[str] = []
        lines.append(f"Plugins: {self.summary()}")
        lines.append("")

        if self.loaded:
            lines.append("Loaded:")
            for p in self.loaded:
                version_str = f" v{p.version}" if p.version else ""
                mp_str = f" [{p.marketplace}]" if p.marketplace else ""
                lines.append(f"  - {p.name}{version_str}{mp_str} ({p.source}: {p.root_path})")
            lines.append("")

        if self.warnings:
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  - {w}")
            lines.append("")

        if self.errors:
            lines.append("Errors:")
            for e in self.errors:
                lines.append(f"  - {e}")
            lines.append("")

        return "\n".join(lines).rstrip()


def build_diagnostics(
    loaded: list[LoadedPlugin],
    warnings: list[PluginWarning],
    errors: list[PluginLoadError],
) -> PluginDiagnostics:
    """Build a diagnostics snapshot from manager state."""
    return PluginDiagnostics(
        loaded=list(loaded),
        warnings=list(warnings),
        errors=list(errors),
    )


def collect_full_plugin_diagnostics(
    *,
    inline_dirs: Optional[List[Path]] = None,
) -> Tuple[List[LoadedPlugin], set[str], "PluginDiagnostics"]:
    """Load plugins **and** simulate registration to surface post-load errors.

    ``PluginManager.load_plugins`` alone misses the failures that only show up
    once ``resolve_plugin_entries`` / ``resolve_plugin_agents`` run — for
    example, skill or agent name collisions that would cause
    ``_load_and_register_plugins`` to reject the plugin at runtime. Returning
    just the manager's view would mean ``agentao doctor`` under-reports
    compared to ``agentao plugin list``.

    Returns ``(loaded, failed_names, diagnostics)``:

    - ``loaded`` is the unfiltered list (callers that need per-plugin
      ``"ok"`` / ``"failed"`` status pair it with ``failed_names``);
    - ``failed_names`` is the set of plugin names that fell out during
      registration simulation;
    - ``diagnostics`` already has resolver warnings/errors folded in and its
      ``loaded`` field contains only healthy plugins.
    """
    # Local imports keep the diagnostics module light on its own load path.
    from .manager import PluginManager
    from .resolvers.agents import resolve_plugin_agents
    from .resolvers.skills import resolve_plugin_entries

    mgr = PluginManager(inline_dirs=inline_dirs)
    loaded = mgr.load_plugins()

    all_warnings = list(mgr.get_warnings())
    all_errors = list(mgr.get_errors())
    failed_plugins: set[str] = set()

    for plugin in loaded:
        _entries, pw, pe = resolve_plugin_entries(plugin)
        all_warnings.extend(pw)
        all_errors.extend(pe)
        if pe:
            failed_plugins.add(plugin.name)

    for plugin in loaded:
        if plugin.name in failed_plugins:
            continue
        _defs, aw, ae = resolve_plugin_agents(plugin)
        all_warnings.extend(aw)
        all_errors.extend(ae)
        if ae:
            failed_plugins.add(plugin.name)

    healthy = [p for p in loaded if p.name not in failed_plugins]
    diag = build_diagnostics(healthy, all_warnings, all_errors)
    return loaded, failed_plugins, diag
