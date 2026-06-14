"""Unit tests for process-scoped verbose state and logging."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest

from untaped import verbose


@pytest.fixture(autouse=True)
def _reset_verbose() -> Iterator[None]:
    logger = logging.getLogger("untaped")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    verbose.reset()
    yield
    verbose.reset()
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)


def test_verbose_defaults_off() -> None:
    assert verbose.is_verbose() is False


def test_enable_turns_verbose_on() -> None:
    verbose.enable()
    assert verbose.is_verbose() is True


def test_reset_turns_verbose_off() -> None:
    verbose.enable()
    verbose.reset()
    assert verbose.is_verbose() is False


def test_reset_undoes_debug_logging() -> None:
    verbose.enable()
    assert logging.getLogger("untaped").level == logging.DEBUG

    verbose.reset()

    assert logging.getLogger("untaped").level != logging.DEBUG


def test_enable_accepts_a_handler_value_and_ignores_it() -> None:
    # The root-option dispatcher calls handlers with a string; flags ignore it.
    verbose.enable("")
    assert verbose.is_verbose() is True


def test_configure_logging_routes_untaped_logger_to_stderr() -> None:
    stream = io.StringIO()
    verbose.configure_logging(logging.DEBUG)
    logger = logging.getLogger("untaped")
    # Point the configured handler at a capture stream to assert routing.
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(stream)

    logging.getLogger("untaped.test").debug("hello debug")

    assert "hello debug" in stream.getvalue()
    assert logger.level == logging.DEBUG
