"""Entry point for ``python -m agentao.acp``.

Issue 01 does not wire ACP into the main ``agentao`` CLI on purpose — that
belongs to Issue 12. For now, running the module directly is enough to
satisfy the "process can start in ACP stdio mode without entering interactive
CLI mode" acceptance criterion and to support smoke testing.

As subsequent issues land, each adds its own ``register(server)`` call below.
"""

import sys

from . import initialize, session_cancel, session_load, session_new, session_prompt
from .server import AcpServer


def _normalize_encoding_name(enc: str | None) -> str:
    """Normalize an encoding name to lowercase with hyphens for comparison."""
    return (enc or "").lower().replace("_", "-")


def _is_utf8(stream) -> bool:  # noqa: ANN001
    """Return True if *stream*'s encoding is UTF-8."""
    return _normalize_encoding_name(getattr(stream, "encoding", None)) == "utf-8"


def _ensure_acp_utf8_stdio() -> None:
    """Force stdin/stdout/stderr to UTF-8 with strict error handling.

    ACP is a JSON-RPC protocol over stdio — every byte on stdin and stdout
    must be valid UTF-8.  Using ``errors="strict"`` ensures that encoding
    problems surface immediately as exceptions rather than silently
    corrupting the protocol stream (e.g. replacing characters with ``?``).

    If reconfiguration fails or the streams cannot be made UTF-8, the
    function writes a diagnostic to stderr and exits with code 1.  The
    diagnostic intentionally avoids stdout so the JSON-RPC channel is
    never polluted.
    """
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="strict")
            except Exception:
                pass  # checked below

    bad = [
        name
        for name in ("stdin", "stdout")
        if not _is_utf8(getattr(sys, name))
    ]
    if bad:
        sys.stderr.write(
            f"ACP requires UTF-8 stdio. Invalid streams: {', '.join(bad)}\n"
        )
        sys.stderr.write(
            "Set PYTHONUTF8=1 or PYTHONIOENCODING=utf-8 and retry.\n"
        )
        sys.stderr.flush()
        raise SystemExit(1)


def main() -> None:
    _ensure_acp_utf8_stdio()
    server = AcpServer()  # attaches to real sys.stdin / sys.stdout with guards
    initialize.register(server)      # Issue 02: initialize handshake
    session_new.register(server)     # Issue 04: session/new creation
    session_prompt.register(server)  # Issue 06: session/prompt turn execution
    session_cancel.register(server)  # Issue 09: session/cancel turn cancellation
    session_load.register(server)    # Issue 10: session/load + history replay
    server.run()


if __name__ == "__main__":
    main()
