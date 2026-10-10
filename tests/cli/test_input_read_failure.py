"""A prompt read that fails ends the session instead of looping.

``run_loop``'s ``except Exception`` reports the error and reads input again.
For a read that cannot succeed that is a busy loop: ``agentao </dev/null`` on
macOS printed ``Error: [Errno 22] Invalid argument`` thousands of times a
second, and an empty pipe did the same with ``EOFError`` (an empty
``Error:``). Ctrl-D on an empty line took the same path.
"""

from __future__ import annotations

import pytest

from agentao.cli.input_loop import run_loop


class _ReadAgain(BaseException):
    """Raised on a second read, so the old loop fails here instead of hanging.

    ``BaseException`` so neither of the loop's handlers can catch it.
    """


class _FailingReadCli:
    def __init__(self, error: BaseException):
        self._error = error
        self.reads = 0
        self.saved_on_exit = 0
        self._staged_images = []
        self._pending_session_start_source = None

    def on_session_start(self, *, source="startup"):
        pass

    def _flush_acp_inbox(self):
        pass

    def _get_user_input(self):
        self.reads += 1
        if self.reads > 1:
            raise _ReadAgain
        raise self._error

    def _save_session_on_exit(self):
        self.saved_on_exit += 1


def test_end_of_input_ends_the_session_like_exit(capsys):
    cli = _FailingReadCli(EOFError())

    run_loop(cli)

    assert cli.reads == 1
    assert cli.saved_on_exit == 1
    assert "Goodbye!" in capsys.readouterr().out


def test_unreadable_stdin_exits_1_after_one_read(capsys):
    cli = _FailingReadCli(OSError(22, "Invalid argument"))

    with pytest.raises(SystemExit) as exc:
        run_loop(cli)

    assert exc.value.code == 1
    assert cli.reads == 1
    # SessionEnd hooks run and the session is saved, as on ``/exit``.
    assert cli.saved_on_exit == 1
    assert "Cannot read input: [Errno 22] Invalid argument" in capsys.readouterr().out
