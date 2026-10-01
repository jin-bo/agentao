"""Plain-terminal stand-ins for the few ``rich`` pieces the login uses.

Used only when ``rich`` is not installed — a bare ``pip install agentao``,
which is what ``uvx agentao@<version> --login`` from an ACP Registry client
gets. They cover the subset ``login`` and ``_llm_prompts`` call: ``Prompt.ask``
(``default``, ``password``, ``choices``), ``Confirm.ask``, ``Panel.fit`` and
``console.print`` with Rich markup, which is stripped rather than rendered.
Input ends the way ``rich`` ends it: ``EOFError`` / ``KeyboardInterrupt``
propagate to the caller.
"""

from __future__ import annotations

import getpass
import re
import sys
from typing import Any, List, Optional

# Rich style tags as this package writes them: ``[bold]``, ``[/dim]``,
# ``[bold cyan]``, ``[/]``. A literal such as ``[y/n]`` does not match.
_MARKUP = re.compile(r"\[/?(?:[a-z]+(?: [a-z]+)*)?\]")


def _plain(text: Any) -> str:
    return _MARKUP.sub("", str(text))


class _Console:
    def print(self, *objects: Any, **_: Any) -> None:
        sys.stdout.write(" ".join(_plain(o) for o in objects) + "\n")
        sys.stdout.flush()


console = _Console()


class _Boxed(str):
    """What :meth:`Panel.fit` returns; printed as a ruled block."""


class Panel:
    @staticmethod
    def fit(text: str, **_: Any) -> _Boxed:
        lines = _plain(text).splitlines() or [""]
        rule = "-" * max(len(line) for line in lines)
        return _Boxed("\n".join([rule, *lines, rule]))


def _read(prompt: str, password: bool) -> str:
    # ``getpass`` reads the terminal itself (``/dev/tty``, or the Windows
    # console), not stdin. When stdin is not a terminal there is nothing to
    # hide and the answer is on stdin, so read it there.
    if password and sys.stdin.isatty():
        return getpass.getpass(prompt)
    return input(prompt)


class Prompt:
    @staticmethod
    def ask(
        prompt: str,
        *,
        default: Optional[str] = None,
        password: bool = False,
        choices: Optional[List[str]] = None,
    ) -> str:
        label = _plain(prompt)
        if choices:
            label += " [" + "/".join(choices) + "]"
        if default:
            label += f" ({default})"
        label += ": "
        while True:
            value = _read(label, password).strip()
            if not value and default:
                return default
            if choices and value not in choices:
                console.print("Please select one of the available options.")
                continue
            return value


class Confirm:
    @staticmethod
    def ask(prompt: str, *, default: bool = False) -> bool:
        label = _plain(prompt) + (" [Y/n]: " if default else " [y/N]: ")
        while True:
            value = input(label).strip().lower()
            if not value:
                return default
            if value in ("y", "yes"):
                return True
            if value in ("n", "no"):
                return False
            console.print("Please enter y or n.")
