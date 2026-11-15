import logging
import sys

from agentao import logging_utils
from agentao.logging_utils import capture_third_party_output, info_once


def test_capture_third_party_output_redirects_stderr(monkeypatch, caplog, capsys):
    target_logger = logging.getLogger("agentao.test.logging_utils")

    def runner():
        print("third-party stderr line", file=sys.stderr)

    with caplog.at_level(logging.DEBUG, logger="agentao.test.logging_utils"):
        capture_third_party_output(
            runner=runner,
            target_logger=target_logger,
            prefix="pkg: ",
        )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert any("pkg: third-party stderr line" in r.message for r in caplog.records)


def test_capture_third_party_output_redirects_named_logger(caplog, capsys):
    target_logger = logging.getLogger("agentao.test.logging_utils")

    def runner():
        logging.getLogger("thirdparty").info("startup complete")

    with caplog.at_level(logging.DEBUG, logger="agentao.test.logging_utils"):
        capture_third_party_output(
            runner=runner,
            source_logger_names=("thirdparty",),
            target_logger=target_logger,
            prefix="pkg: ",
        )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert any("pkg: startup complete" in r.message for r in caplog.records)


def test_capture_third_party_output_restores_logger_state():
    source_logger = logging.getLogger("thirdparty.restore")
    original_handler = logging.NullHandler()
    source_logger.handlers = [original_handler]
    source_logger.setLevel(logging.WARNING)
    source_logger.propagate = True
    source_logger.disabled = True

    capture_third_party_output(
        runner=lambda: None,
        source_logger_names=("thirdparty.restore",),
        target_logger=logging.getLogger("agentao.test.logging_utils"),
    )

    assert source_logger.handlers == [original_handler]
    assert source_logger.level == logging.WARNING
    assert source_logger.propagate is True
    assert source_logger.disabled is True


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.seen: list[str] = []

    def emit(self, record):
        self.seen.append(record.getMessage())


def _isolated_logger(monkeypatch, name: str) -> logging.Logger:
    """A named logger cut off from the root, restored after the test."""
    log = logging.getLogger(name)
    monkeypatch.setattr(log, "propagate", False)
    monkeypatch.setattr(log, "level", logging.INFO)
    # Assigning ``level`` skips the logger's ``isEnabledFor`` cache; reset it.
    log.setLevel(logging.INFO)
    return log


def test_info_once_logs_each_key_once(monkeypatch):
    log = _isolated_logger(monkeypatch, "agentao.test.info_once")
    handler = _Collect()
    log.addHandler(handler)
    try:
        info_once(log, "test:a", "note %s", "a")
        info_once(log, "test:a", "note %s", "a")
        info_once(log, "test:b", "note %s", "b")
    finally:
        log.removeHandler(handler)
    assert handler.seen == ["note a", "note b"]


def test_info_once_with_no_handler_to_reach_is_not_spent(monkeypatch):
    """A note emitted before any handler exists is not counted as logged:
    the first emit that a handler can receive is the one that counts."""
    log = _isolated_logger(monkeypatch, "agentao.test.info_once_unattached")
    info_once(log, "test:unattached", "note")
    assert "test:unattached" not in logging_utils._INFO_ONCE_KEYS

    handler = _Collect()
    log.addHandler(handler)
    try:
        info_once(log, "test:unattached", "note")
        info_once(log, "test:unattached", "note")
    finally:
        log.removeHandler(handler)
    assert handler.seen == ["note"]
