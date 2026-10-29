"""Through the root: the exit code is derived from what a plugin command emitted."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from core_test_support.commands.support import make_plugin
from untaped.runtime import bootstrap
from untaped.sdk import Command, Diagnostics
from untaped.sdk._batch import finish
from untaped.sdk.errors import ConfigError
from untaped.sdk.records import Failure
from untaped.sdk.testing import CliInvoker, CliResult, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


#: what ``untaped toy go`` runs
BODY: list[Callable[[], None]] = []


class Go(Command):
    """Run the body."""

    def run(self) -> None:
        BODY[0]()


def _run(body: Callable[[], None]) -> CliResult:
    BODY[:] = [body]
    toy = make_plugin("toy", commands=(Go,))
    root = bootstrap.build_root_app(candidates=(plugin_candidate(toy),))
    return CliInvoker().invoke(root.meta, ["toy", "go"])


def test_a_command_that_emits_a_failure_and_returns_exits_with_it() -> None:
    def body() -> None:
        Diagnostics().failure(ConfigError("token rejected", category="auth"))

    result = _run(body)

    assert result.exit_code == 4, result.output
    assert result.stderr == "error: token rejected\n"


def test_a_plain_failure_exits_1_and_a_warning_exits_0() -> None:
    assert _run(lambda: Diagnostics().warning("careful")).exit_code == 0
    assert _run(lambda: Diagnostics().failure(Failure.plain("broke"))).exit_code == 1


@pytest.mark.parametrize("code", [1, 3])
def test_an_explicit_exit_never_hides_a_counted_failure(code: int) -> None:
    def body() -> None:
        Diagnostics().failure(ConfigError("token rejected", category="auth"))
        raise SystemExit(code)

    assert _run(body).exit_code == 4


def test_a_predicate_hit_exits_3_without_a_failure_and_1_with_one() -> None:
    assert _run(lambda: finish(False, predicate_hit=True)).exit_code == 3

    def body() -> None:
        Diagnostics().failure(Failure.plain("x: unreadable"))
        finish(False, predicate_hit=True)

    assert _run(body).exit_code == 1
