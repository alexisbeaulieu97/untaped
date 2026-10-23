"""Unit tests for process-scoped verbose state and logging."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from contextlib import redirect_stderr

import pytest

from untaped.runtime import verbose
from untaped.runtime.output_policy import is_verbose


@pytest.fixture(autouse=True)
def _reset_verbose() -> Iterator[None]:
    logger = logging.getLogger("untaped")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    verbose.reset()
    yield
    verbose.reset()
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate


def test_verbose_defaults_off() -> None:
    assert is_verbose() is False


def test_enable_turns_verbose_on() -> None:
    verbose.enable()
    assert is_verbose() is True


def test_reset_turns_verbose_off() -> None:
    verbose.enable()
    verbose.reset()
    assert is_verbose() is False


def test_reset_undoes_debug_logging() -> None:
    verbose.enable()
    assert logging.getLogger("untaped").level == logging.DEBUG

    verbose.reset()

    assert logging.getLogger("untaped").level != logging.DEBUG


def test_enable_accepts_a_handler_value_and_ignores_it() -> None:
    # The root-option dispatcher calls handlers with a string; flags ignore it.
    verbose.enable("")
    assert is_verbose() is True


def test_verbose_routes_the_untaped_logger_to_stderr_as_debug_lines() -> None:
    stream = io.StringIO()
    token = verbose.enable()
    try:
        with redirect_stderr(stream):
            logging.getLogger("untaped.test").debug("hello debug")
        assert logging.getLogger("untaped").level == logging.DEBUG
    finally:
        verbose.reset(token)

    assert stream.getvalue() == "DEBUG untaped.test: hello debug\n"


def test_a_debug_line_without_verbose_is_muted() -> None:
    stream = io.StringIO()
    verbose.configure_logging(logging.DEBUG)
    try:
        with redirect_stderr(stream):
            logging.getLogger("untaped.test").debug("hello debug")
    finally:
        verbose.reset()

    assert stream.getvalue() == ""
