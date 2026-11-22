"""``/skills`` checkbox picker — turn skills on or off from one list.

Each row is ``[x] name  description``. Typing filters the list, ↑/↓ move,
Space or Enter toggles the row under the cursor, Esc or Ctrl+C closes.

**Each toggle is saved at once.** It calls ``SkillManager.disable_skill`` /
``enable_skill``, the same calls ``/skills disable|enable`` makes, so the
cross-process lock, the atomic write, and the "a failed write changes
nothing" rule all come from the manager. The picker never writes
``skills_config.json`` itself. When the manager answers ``Error…`` the row
goes back to its old state and shows the message.

No model request runs while the picker is open, so saving per toggle costs
at most one rebuild of the cached prompt prefix, on the next turn.

The state lives in :class:`SkillsPickerModel`, which has no prompt_toolkit
import, so tests drive it without a terminal. :func:`run_skills_picker`
wraps it in a small inline prompt_toolkit ``Application``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..security.terminal_text import sanitize_terminal_text

#: Rows shown at once; the window scrolls to keep the cursor in view.
MAX_VISIBLE_ROWS = 12

#: Characters of a description shown on one row.
_DESC_CHARS = 80


@dataclass
class PickerRow:
    name: str
    description: str
    enabled: bool
    active: bool
    #: ``local`` (a discovered skill), ``mcp`` (an ``mcp:`` skill), or
    #: ``missing`` (a disabled name the last scan did not find, #270).
    kind: str
    note: str = ""


def _one_line(text: Any) -> str:
    """Untrusted text (SKILL.md, an MCP server) as one clean display line."""
    text = sanitize_terminal_text(str(text or ""))
    return " ".join(text.split())


class SkillsPickerModel:
    """Rows, filter, cursor and toggles of the picker; no terminal I/O."""

    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.rows: List[PickerRow] = self._build_rows()
        self.initial: Dict[str, bool] = {r.name: r.enabled for r in self.rows}
        #: Names this picker toggled. The summary counts only these, so a
        #: change another process wrote (adopted on resync) is not reported
        #: as one made here.
        self.touched: set = set()
        self.query = ""
        self.cursor = 0
        self.scroll = 0
        self.filtered: List[int] = list(range(len(self.rows)))

    # -- construction ---------------------------------------------------

    def _disabled(self) -> set:
        # ``or set()``: a host-injected manager may carry ``None`` here.
        return set(getattr(self.manager, "disabled_skills", None) or ())

    def _active(self) -> dict:
        return dict(getattr(self.manager, "active_skills", None) or {})

    def _build_rows(self) -> List[PickerRow]:
        available = dict(getattr(self.manager, "available_skills", None) or {})
        disabled = self._disabled()
        active = self._active()
        # A skill loaded by URI is active with no catalogue entry, and a
        # stale disabled name has neither — both must still be listed, or
        # the picker hides what ``/skills list`` shows.
        names = set(available) | disabled | {
            n for n in active if str(n).startswith("mcp:")
        }
        rows = []
        for name in sorted(names):
            info = available.get(name) or {}
            if name in available:
                kind = "mcp" if info.get("source_kind") == "mcp" else "local"
            elif name in active:
                kind = "mcp"
            else:
                kind = "missing"
            rows.append(PickerRow(
                name=name,
                description=_one_line(info.get("description", "")),
                enabled=name not in disabled,
                active=name in active,
                kind=kind,
            ))
        return rows

    # -- filter and cursor ----------------------------------------------

    def set_query(self, query: str) -> None:
        current = self.current_row()
        self.query = query
        needle = query.strip().lower()
        if not needle:
            self.filtered = list(range(len(self.rows)))
        else:
            name_hits = [i for i, r in enumerate(self.rows) if needle in r.name.lower()]
            desc_hits = [
                i for i, r in enumerate(self.rows)
                if i not in name_hits and needle in r.description.lower()
            ]
            self.filtered = name_hits + desc_hits
        # Keep the cursor on the same skill when it survives the filter.
        if current is not None and self.rows.index(current) in self.filtered:
            self.cursor = self.filtered.index(self.rows.index(current))
        else:
            self.cursor = 0
        self._clamp()

    def type_char(self, ch: str) -> None:
        self.set_query(self.query + ch)

    def backspace(self) -> None:
        if self.query:
            self.set_query(self.query[:-1])

    def move(self, delta: int) -> None:
        if not self.filtered:
            return
        self.cursor = (self.cursor + delta) % len(self.filtered)
        self._clamp()

    def _clamp(self) -> None:
        n = len(self.filtered)
        self.cursor = max(0, min(self.cursor, n - 1)) if n else 0
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + MAX_VISIBLE_ROWS:
            self.scroll = self.cursor - MAX_VISIBLE_ROWS + 1
        self.scroll = max(0, min(self.scroll, max(0, n - MAX_VISIBLE_ROWS)))

    def current_row(self) -> Optional[PickerRow]:
        if not self.filtered:
            return None
        return self.rows[self.filtered[self.cursor]]

    # -- toggle ---------------------------------------------------------

    def toggle(self) -> None:
        row = self.current_row()
        if row is None:
            return
        if row.kind == "missing" and row.enabled:
            # Cleared already. The manager refuses to disable a name the
            # scan did not find, with a message that lists every skill.
            row.note = "cleared; not found by the last scan, so it cannot be disabled here"
            return
        try:
            if row.enabled:
                result = self.manager.disable_skill(row.name)
            else:
                result = self.manager.enable_skill(row.name)
        except Exception as exc:  # a host manager may raise; the row stays put
            result = f"Error: {type(exc).__name__}: {exc}"
        result = str(result)
        if result.startswith("Error"):
            row.note = _one_line(result)
            return
        row.note = ""
        self.touched.add(row.name)
        self._resync()

    def _resync(self) -> None:
        """Re-read state from the manager after a toggle.

        The manager adopts the whole set the file holds, so a disable
        another process wrote shows up here, and a disable deactivates an
        active skill — every row follows, not only the toggled one.
        """
        disabled = self._disabled()
        active = self._active()
        for r in self.rows:
            r.enabled = r.name not in disabled
            r.active = r.name in active

    # -- summary --------------------------------------------------------

    def changes(self) -> tuple:
        touched = [r for r in self.rows if r.name in self.touched]
        enabled = sum(
            1 for r in touched if r.enabled and not self.initial.get(r.name, True)
        )
        disabled = sum(
            1 for r in touched if not r.enabled and self.initial.get(r.name, True)
        )
        return enabled, disabled

    def summary(self) -> str:
        enabled, disabled = self.changes()

        def n(count: int) -> str:
            return f"{count} skill" + ("" if count == 1 else "s")

        if enabled and disabled:
            return f"Enabled {n(enabled)}, disabled {n(disabled)}."
        if enabled:
            return f"Enabled {n(enabled)}."
        if disabled:
            return f"Disabled {n(disabled)}."
        return ""

    # -- rendering ------------------------------------------------------

    def fragments(self) -> List[tuple]:
        """prompt_toolkit formatted-text fragments for the whole picker."""
        out: List[tuple] = [
            ("bold", "Enable/Disable Skills\n"),
            ("class:dim", "Turn skills on or off. Each change is saved at once.\n"),
        ]
        if self.query:
            out.append(("", f"Search: {self.query}\n"))
        else:
            out.append(("class:dim", "Type to search skills\n"))

        if not self.filtered:
            out.append(("class:dim", "  no matches\n"))
        window = self.filtered[self.scroll:self.scroll + MAX_VISIBLE_ROWS]
        for pos, idx in enumerate(window, start=self.scroll):
            r = self.rows[idx]
            selected = pos == self.cursor
            style = "class:selected" if selected else ""
            prefix = "›" if selected else " "
            mark = "x" if r.enabled else " "
            out.append((style, f"{prefix} [{mark}] {_one_line(r.name)}"))
            tags = []
            if r.active:
                tags.append("active")
            if r.kind == "mcp":
                tags.append("mcp")
            if r.kind == "missing":
                tags.append("missing")
            if tags:
                out.append(("class:tag", f" ({', '.join(tags)})"))
            if r.note:
                out.append(("class:error", f"  {r.note}"))
            elif selected and r.active and r.enabled:
                out.append(("class:dim", "  disabling also deactivates it"))
            elif r.description:
                desc = r.description
                if len(desc) > _DESC_CHARS:
                    desc = desc[:_DESC_CHARS - 1] + "…"
                out.append(("class:dim", f"  {desc}"))
            out.append(("", "\n"))
        hidden = len(self.filtered) - len(window)
        if hidden > 0:
            out.append(("class:dim", f"  … {hidden} more\n"))
        out.append(("class:dim", "↑/↓ move · Space/Enter toggle · Esc close"))
        return out


def build_skills_picker_app(model: SkillsPickerModel, **app_kwargs: Any):
    """The inline prompt_toolkit ``Application`` around ``model``.

    ``app_kwargs`` passes ``input=`` / ``output=`` through, for tests.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    def _up(event):
        model.move(-1)

    @kb.add("down")
    @kb.add("c-n")
    def _down(event):
        model.move(1)

    @kb.add("space")
    @kb.add("enter")
    def _toggle(event):
        model.toggle()

    @kb.add("backspace")
    def _back(event):
        model.backspace()

    @kb.add("escape", eager=True)
    @kb.add("c-c")
    def _close(event):
        event.app.exit()

    @kb.add(Keys.Any)
    def _type(event):
        ch = event.data
        if ch and ch.isprintable() and not ch.isspace():
            model.type_char(ch)

    style = Style.from_dict({
        "dim": "#888888",
        "selected": "bold #00afff",
        "tag": "#5f87af",
        "error": "#ff5f5f",
    })
    control = FormattedTextControl(model.fragments, show_cursor=False)
    return Application(
        layout=Layout(Window(content=control, dont_extend_height=True)),
        key_bindings=kb,
        style=style,
        full_screen=False,
        erase_when_done=True,
        **app_kwargs,
    )


def run_skills_picker(manager: Any) -> SkillsPickerModel:
    """Show the picker until closed; return the model for the summary.

    With no rows nothing is shown, and the caller says so.
    """
    model = SkillsPickerModel(manager)
    if model.rows:
        build_skills_picker_app(model).run()
    return model
