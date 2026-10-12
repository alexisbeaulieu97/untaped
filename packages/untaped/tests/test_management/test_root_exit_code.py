"""Through the root: the exit code is derived from what a plugin command emitted."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.batch import finish
from untaped.errors import ConfigError
from untaped.sdk import PluginSpec, app_context, create_app
from untaped.testing import CliInvoker, CliResult, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _run(body: Callable[[], None]) -> CliResult:
    def factory() -> App:
        app = create_app(name="toy", help="Toy plugin.")

        @app.command(name="go")
        def go() -> None:
            """Run the body."""
            body()

        return app

    spec = PluginSpec(name="toy", app_factory=factory, help="Toy.")
    root = bootstrap.build_root_app(candidates=(plugin_candidate(spec),))
    return CliInvoker().invoke(root.meta, ["toy", "go"])


def test_a_command_that_emits_a_failure_and_returns_exits_with_it() -> None:
    def body() -> None:
        app_context().diag.failure(ConfigError("token rejected", category="auth"))

    result = _run(body)

    assert result.exit_code == 4, result.output
    assert result.stderr == "error: token rejected\n"


def test_a_plain_error_line_exits_1_and_a_warning_exits_0() -> None:
    assert _run(lambda: app_context().diag.warning("careful")).exit_code == 0
    assert _run(lambda: app_context().ui().message("error", "broke")).exit_code == 1


@pytest.mark.parametrize("code", [1, 3])
def test_an_explicit_exit_never_hides_a_counted_failure(code: int) -> None:
    def body() -> None:
        app_context().diag.failure(ConfigError("token rejected", category="auth"))
        raise SystemExit(code)

    assert _run(body).exit_code == 4


def test_a_predicate_hit_exits_3_without_a_failure_and_1_with_one() -> None:
    assert _run(lambda: finish(False, predicate_hit=True)).exit_code == 3

    def body() -> None:
        app_context().ui().message("error", "x: unreadable")
        finish(False, predicate_hit=True)

    assert _run(body).exit_code == 1
