import pytest
from untaped_core.logging import configure_logging, get_logger


def test_get_logger_returns_loguru_logger() -> None:
    logger = get_logger("untaped_core.tests")
    assert hasattr(logger, "info")
    assert hasattr(logger, "error")


def test_configure_logging_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO")
    logger = get_logger("untaped_core.tests")
    logger.info("hello-stderr")
    captured = capsys.readouterr()
    assert "hello-stderr" in captured.err
    assert "hello-stderr" not in captured.out
