"""Reading a launch without caring which shape it took.

``LegacyLaunch`` and ``WindowsLaunch`` name the interpreter in different fields, and which
one a spec produces is platform-dependent: a POSIX interpreter configured on Windows cannot
go through ``LegacyLaunch``, because ``shell=True`` there composes cmd's ``/c`` and an
interpreter that is not cmd reads it as a filename. A test that asserts *which interpreter
will run* should not have to restate that split.
"""

from __future__ import annotations

from agentao.capabilities.shell_spec import LaunchRequest, LegacyLaunch


def interpreter_of(launch: LaunchRequest) -> str | None:
    """The image this launch will start, whichever field carries it."""
    if isinstance(launch, LegacyLaunch):
        return None if launch.executable is None else str(launch.executable)
    return str(launch.application_name)


def body_of(launch: LaunchRequest) -> str:
    """The command line the child receives — the body on the legacy path, the built line
    otherwise. Not the model's text: for PowerShell that line is base64."""
    if isinstance(launch, LegacyLaunch):
        return launch.command
    return launch.command_line
