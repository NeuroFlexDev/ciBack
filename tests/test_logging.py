import logging

import log


class DummySettings:
    LOG_LEVEL = "DEBUG"


def test_get_logger_uses_runtime_log_level(monkeypatch):
    logger_name = "test.pr1.logger"
    logger = logging.getLogger(logger_name)
    logger.handlers.clear()

    monkeypatch.setattr(log, "settings", DummySettings())

    configured = log.get_logger(logger_name)

    assert configured.level == logging.DEBUG
    assert configured.handlers
    assert configured.handlers[0].level == logging.DEBUG


def test_request_id_filter_injects_context_value():
    record = logging.LogRecord(
        name="test.pr1.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    log.set_request_id("req-pr1")
    assert log.RequestIdFilter().filter(record) is True
    assert record.request_id == "req-pr1"
