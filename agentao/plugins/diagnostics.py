"""Plugin diagnostics snapshot for CLI, logging, and testing."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import LoadedPlugin, PluginLoadError, PluginWarning


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
