"""Agentao - A governed agent runtime for local-first, private-first, embeddable AI agents."""

import os
import sys
import warnings
from typing import TYPE_CHECKING

warnings.filterwarnings("ignore", message="urllib3.*or chardet.*doesn't match")

__version__ = "0.4.8.dev0"


def _ensure_utf8() -> None:
    """Force UTF-8 on Windows console streams (no-op on POSIX).

    Windows defaults to a legacy code page (cp1252 / gb2312 / cp932 / …)
    for console I/O. Python inherits that encoding when ``PYTHONIOENCODING``
    is unset, so any non-ASCII output — curly quotes from skill metadata,
    CJK file paths, model output emoji — raises ``UnicodeEncodeError``
    before it reaches the user. Embedded hosts that ``import agentao``
    from a Windows console expect sane behavior without setting ``-X utf8``
    or environment variables themselves.
    """
    if sys.platform != "win32":
        return

    # ``setdefault`` so a deliberate ``PYTHONIOENCODING=cp936`` override survives.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    try:  # pragma: no cover - Windows-only path
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleOutputCP(65001)  # CP_UTF8
        kernel32.SetConsoleCP(65001)
    except (OSError, AttributeError):
        pass

    for stream, errors in (
        (sys.stdin, "replace"),
        (sys.stdout, "backslashreplace"),
        (sys.stderr, "backslashreplace"),
    ):
        try:
            stream.reconfigure(encoding="utf-8", errors=errors)  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass


_ensure_utf8()

# Lazy exports via PEP 562 module __getattr__.
#
# Eager imports of `Agentao` / `SkillManager` would pull the entire LLM stack
# (openai, mcp, tools, llm.client) into every consumer of the `agentao` package
# — including standalone subpackages like `agentao.memory` that have no LLM
# dependency. Lazy resolution lets `import agentao.memory` stay lightweight
# and independently testable, while keeping `from agentao import Agentao` and
# `agentao.Agentao(...)` working unchanged.

__all__ = ["Agentao", "SkillManager"]

if TYPE_CHECKING:
    # Type checkers and IDEs see explicit imports; the runtime path uses __getattr__.
    from .agent import Agentao
    from .skills import SkillManager


def __getattr__(name: str):
    if name == "Agentao":
        from .agent import Agentao
        return Agentao
    if name == "SkillManager":
        from .skills import SkillManager
        return SkillManager
    raise AttributeError(f"module 'agentao' has no attribute {name!r}")


def __dir__():
    return sorted(set(list(globals().keys()) + __all__))
