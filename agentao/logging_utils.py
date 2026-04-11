"""Helpers for routing noisy third-party startup output into Agentao logs."""

from __future__ import annotations

import contextlib
import io
import logging
from collections.abc import Callable, Iterable


def capture_third_party_output(
    *,
    runner: Callable[[], None],
    source_logger_names: Iterable[str] = (),
    target_logger: logging.Logger,
    target_level: int = logging.DEBUG,
    prefix: str | None = None,
    capture_stderr: bool = True,
) -> None:
    """Run ``runner`` while capturing noisy third-party logging/stderr output.

    Any line emitted to stderr or by the named third-party loggers during the
    call is replayed into ``target_logger`` at ``target_level``. This keeps
    startup/progress noise out of the terminal while still preserving it in the
    file log.
    """
    buf = io.StringIO()
    handlers: list[tuple[logging.Logger, list[logging.Handler], int, bool, bool]] = []

    for name in source_logger_names:
        source_logger = logging.getLogger(name)
        handler = logging.StreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handlers.append(
            (
                source_logger,
                list(source_logger.handlers),
                source_logger.level,
                source_logger.propagate,
                source_logger.disabled,
            )
        )
        source_logger.handlers = [handler]
        source_logger.setLevel(logging.DEBUG)
        source_logger.propagate = False
        source_logger.disabled = False

    try:
        if capture_stderr:
            with contextlib.redirect_stderr(buf):
                runner()
        else:
            runner()
    finally:
        for source_logger, old_handlers, old_level, old_propagate, old_disabled in handlers:
            for handler in source_logger.handlers:
                handler.flush()
            source_logger.handlers = old_handlers
            source_logger.setLevel(old_level)
            source_logger.propagate = old_propagate
            source_logger.disabled = old_disabled

    for line in buf.getvalue().splitlines():
        line = line.strip()
        if not line:
            continue
        message = f"{prefix}{line}" if prefix else line
        target_logger.log(target_level, message)
