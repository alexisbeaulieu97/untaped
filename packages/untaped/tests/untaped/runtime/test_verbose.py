"""``--verbose`` on the run, and logging through the stderr sink."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from contextlib import redirect_stderr

import pytest
from pydantic import BaseModel

from untaped.runtime import bootstrap, verbose
from untaped.runtime.registry import PluginCandidate
from untaped.runtime.run import entered, start_run
from untaped.sdk import Command, Plugin
from untaped.sdk._run import is_verbose

pytestmark = pytest.mark.usefixtures("home_run")


@pytest.fixture(autouse=True)
def _kept_logger() -> Iterator[None]:
    logger = logging.getLogger("untaped")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    yield
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate


def test_verbose_defaults_off() -> None:
    assert is_verbose() is False


def test_enable_turns_verbose_on_for_the_run() -> None:
    with entered(start_run()) as run:
        verbose.enable(run)
        assert is_verbose() is True
        assert run.policy.verbose
    assert is_verbose() is False


def test_the_end_of_a_verbose_run_undoes_debug_logging() -> None:
    with entered(start_run()) as run:
        verbose.enable(run)
        assert logging.getLogger("untaped").level == logging.DEBUG

    assert logging.getLogger("untaped").level != logging.DEBUG


def test_a_nested_verbose_run_leaves_the_logger_as_the_outer_run_asks() -> None:
    logger = logging.getLogger("untaped")
    with entered(start_run()) as outer:
        verbose.enable(outer)
        with entered(start_run()):
            pass  # a quiet one ending changes nothing
        assert logger.level == logging.DEBUG
        with entered(start_run()) as nested:
            verbose.enable(nested)
        assert logger.level == logging.DEBUG
    with entered(start_run()), entered(start_run()) as nested:
        verbose.enable(nested)
    assert logger.level != logging.DEBUG


def test_enable_accepts_a_handler_value_and_ignores_it() -> None:
    # The token pass calls handlers with a string; flags ignore it.
    with entered(start_run()) as run:
        verbose.enable(run, "")
        assert is_verbose() is True


def test_verbose_routes_the_untaped_logger_to_stderr_as_debug_lines() -> None:
    stream = io.StringIO()
    with entered(start_run()) as run:
        verbose.enable(run)
        with redirect_stderr(stream):
            logging.getLogger("untaped.test").debug("hello debug")
        assert logging.getLogger("untaped").level == logging.DEBUG

    assert stream.getvalue() == "DEBUG untaped.test: hello debug\n"


def test_a_debug_line_without_verbose_is_muted() -> None:
    stream = io.StringIO()
    verbose.configure_logging(logging.DEBUG)
    try:
        with redirect_stderr(stream):
            logging.getLogger("untaped.test").debug("hello debug")
    finally:
        verbose.restore()

    assert stream.getvalue() == ""


#: The ``untaped`` logger's level each command below saw, in the order they ran.
levels: list[int] = []


class ProbeSettings(BaseModel):
    """Nothing to configure."""


class Peek(Command):
    """Note the logger's level."""

    def run(self) -> None:
        levels.append(logging.getLogger("untaped").level)


class Nest(Command):
    """Call the root with ``-v`` from inside the command, then note the level once it returns."""

    def run(self) -> None:
        with bootstrap.kept_composition(), pytest.raises(SystemExit):
            bootstrap.run_root(["-v", "probe", "peek"], candidates=[PROBE])
        levels.append(logging.getLogger("untaped").level)


class Probe(Plugin):
    """Note the logger's level, directly and around a nested root."""

    settings = ProbeSettings
    commands = (Peek, Nest)


PROBE = PluginCandidate(distribution="untaped-probe", name="probe", target=Probe)


def _run_root(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        bootstrap.run_root(argv, candidates=[PROBE])
    assert exit_info.value.code in (0, None)


@pytest.mark.usefixtures("_isolated_config", "fresh_composition")
def test_the_logger_level_follows_each_root_invocation_s_own_verbose() -> None:
    levels.clear()
    logger = logging.getLogger("untaped")

    _run_root(["-v", "probe", "peek"])
    _run_root(["probe", "peek"])  # a plain root after a -v one
    assert levels == [logging.DEBUG, logging.NOTSET]
    assert logger.level == logging.NOTSET

    levels.clear()
    _run_root(["-v", "probe", "nest"])  # a -v root inside a -v command
    assert levels == [logging.DEBUG, logging.DEBUG]  # the nested peek, then nest after it
    assert logger.level == logging.NOTSET
