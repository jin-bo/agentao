import logging
import sys

from agentao.logging_utils import capture_third_party_output


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
