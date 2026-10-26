"""A run's output and policy: built with the run, its flags, a child's, a nested invocation's."""

from __future__ import annotations

import contextvars
import io
import threading
from contextlib import redirect_stderr

import pytest
from cyclopts import App

from untaped.runtime.run import child_run, enable_quiet, entered, start_run
from untaped.runtime.sink import Diagnostics, exit_on_counted_failure, failure_exit_code
from untaped.runtime.theme import BUILTIN_THEMES
from untaped.sdk._cli import note_requested_format
from untaped.sdk._run import NoRunError, OutputPolicy, Rendering, SettingsOverlay, is_quiet
from untaped.sdk.errors import ConfigError
from untaped.sdk.testing import invoke_cli


def test_quiet_is_off_outside_a_run_and_by_default() -> None:
    assert is_quiet() is False
    with entered(start_run()):
        assert is_quiet() is False


def test_quiet_is_the_runs_own_and_ends_with_it() -> None:
    with entered(start_run()) as run:
        enable_quiet()
        assert run.policy.quiet and is_quiet()
        with entered(start_run()):
            assert is_quiet() is False  # another invocation's run
        assert is_quiet() is True
    assert is_quiet() is False


def test_a_flag_needs_a_run() -> None:
    with pytest.raises(NoRunError):
        enable_quiet()


def test_the_environment_is_read_when_the_run_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    built = start_run()
    monkeypatch.delenv("UNTAPED_DIAGNOSTICS")

    assert built.policy.diagnostics == "json"
    assert start_run().policy.diagnostics == "text"


def test_the_environment_forces_the_diagnostics_either_way() -> None:
    assert OutputPolicy(forced="text", format="json").diagnostics == "text"
    assert OutputPolicy(forced="json").diagnostics == "json"
    assert OutputPolicy(format="yaml").diagnostics == "json"
    assert OutputPolicy(format="table").diagnostics == "text"


def test_a_fixed_rendering_fixes_the_policy_and_an_11x_note_changes_nothing() -> None:
    theme = BUILTIN_THEMES["high-contrast"]
    with entered(start_run()) as run:
        note_requested_format(["-f", "json"])  # the 11.x seam's phase 1, before it is fixed
        assert run.policy.diagnostics == "json"
        run.render_with(Rendering(format="table", theme=theme))
        note_requested_format(["-f", "json"])

        assert (run.policy.format, run.policy.theme) == ("table", theme)


def test_a_policy_with_a_theme_still_hashes() -> None:
    theme = BUILTIN_THEMES["high-contrast"]
    assert hash(OutputPolicy(theme=theme)) == hash(OutputPolicy())
    assert OutputPolicy(theme=theme) != OutputPolicy()


def test_a_child_run_shares_its_parents_output_and_policy() -> None:
    with redirect_stderr(io.StringIO()), entered(start_run()) as run:
        enable_quiet()
        with child_run(overlay=SettingsOverlay("default", "hello", {})) as child:
            assert child.diag is run.diag
            assert child.policy.quiet
            Diagnostics().failure(ConfigError("in the child", category="auth"))
        assert failure_exit_code() == 4


def test_a_nested_invocation_counts_its_own_failures() -> None:
    app = App(name="inner")

    @app.default
    def _fail() -> None:
        Diagnostics().failure(ConfigError("inner", category="auth"))

    with entered(start_run()):
        first, second = invoke_cli(app, []), invoke_cli(app, [])
        exit_on_counted_failure()  # each invocation's failure stayed its own

    assert first.exit_code == second.exit_code == 4
    assert len(second.diagnostics) == 1


def test_a_worker_with_a_copied_context_writes_to_its_invocations_run() -> None:
    def work() -> None:
        Diagnostics().failure(ConfigError("from the worker", category="auth"))

    with redirect_stderr(io.StringIO()), entered(start_run()):
        worker = threading.Thread(target=contextvars.copy_context().run, args=(work,))
        worker.start()
        worker.join(10.0)
        assert failure_exit_code() == 4
