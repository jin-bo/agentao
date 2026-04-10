"""Module entry point so ``python -m agentao`` works.

This is a thin shim around :func:`agentao.cli.entrypoint` so users can
launch Agentao via either:

  - the installed console script (``agentao [args]``), or
  - ``python -m agentao [args]`` — useful when the console script is
    not on PATH (CI, virtualenvs without activation, vendored installs).

Both paths land in the same argparse, so ``python -m agentao --acp --stdio``
launches the ACP server (Issue 12) just like ``agentao --acp --stdio``.
"""

from agentao.cli import entrypoint


if __name__ == "__main__":
    entrypoint()
