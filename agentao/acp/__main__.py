"""Entry point for ``python -m agentao.acp``.

Issue 01 does not wire ACP into the main ``agentao`` CLI on purpose — that
belongs to Issue 12. For now, running the module directly is enough to
satisfy the "process can start in ACP stdio mode without entering interactive
CLI mode" acceptance criterion and to support smoke testing.

As subsequent issues land, each adds its own ``register(server)`` call below.
"""

from . import initialize, session_cancel, session_load, session_new, session_prompt
from .server import AcpServer


def main() -> None:
    server = AcpServer()  # attaches to real sys.stdin / sys.stdout with guards
    initialize.register(server)      # Issue 02: initialize handshake
    session_new.register(server)     # Issue 04: session/new creation
    session_prompt.register(server)  # Issue 06: session/prompt turn execution
    session_cancel.register(server)  # Issue 09: session/cancel turn cancellation
    session_load.register(server)    # Issue 10: session/load + history replay
    server.run()


if __name__ == "__main__":
    main()
