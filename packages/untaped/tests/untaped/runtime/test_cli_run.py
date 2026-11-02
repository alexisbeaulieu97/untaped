"""Core runs a cyclopts app: a clean error, its exit code, a closed pipe."""

import os
import subprocess
import sys

import pytest
from cyclopts import App

from untaped.runtime.cli_run import report_errors, run_cyclopts_app
from untaped.sdk import HttpError, HttpTransportError, UntapedError
from untaped.sdk.testing import CliInvoker


def test_clean_message_for_untaped_error() -> None:
    app = App(name="test")

    @app.default
    def boom() -> None:
        with report_errors():
            raise UntapedError("something went wrong")

    result = CliInvoker().invoke(app, [])
    assert result.exit_code == 1
    assert "error: something went wrong" in (result.output or result.stderr)


def test_passes_through_non_untaped_exception() -> None:
    """Non-UntapedError exceptions should not be swallowed — they're bugs."""
    app = App(name="test")

    @app.default
    def boom() -> None:
        with report_errors():
            raise ValueError("bug")

    result = CliInvoker().invoke(app, [])
    assert result.exit_code != 0
    # The bug-style exception should bubble up
    assert isinstance(result.exception, ValueError)


# ---- BrokenPipe handling -------------------------------------------------


def test_broken_pipe_from_command_exits_cleanly() -> None:
    """A consumer closing the pipe mid-write surfaces as ``BrokenPipeError``;
    it must convert to a quiet ``SystemExit(0)`` (``| head`` is not a
    failure), not leak as a traceback."""
    app = App(name="test")

    @app.default
    def boom() -> None:
        raise BrokenPipeError(32, "Broken pipe")

    result = CliInvoker().invoke(app, [])
    assert result.exit_code == 0
    assert result.stderr == ""
    # Clean exit — NOT a leaked BrokenPipeError bubbling up as a bug.
    assert isinstance(result.exception, SystemExit)


def test_broken_pipe_at_final_flush_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The common case: the producer's buffered stdout flush fails because the
    consumer already closed the pipe. ``run_cyclopts_app`` forces the flush so
    the failure surfaces here (not at uncatchable interpreter shutdown) and
    exits cleanly. ``fileno()`` raising stands in for a stream with no real fd
    (StringIO, etc.) and exercises the devnull-redirect guard."""
    app = App(name="test")

    @app.default
    def ok() -> None:
        pass

    class _BrokenStdout:
        def write(self, _s: str) -> int:
            return 0

        def flush(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self) -> int:
            raise ValueError("no fd")

    monkeypatch.setattr(sys, "stdout", _BrokenStdout())
    with pytest.raises(SystemExit) as exc:
        run_cyclopts_app(app, [])
    assert exc.value.code == 0


def test_failure_while_handling_another_broken_pipe_keeps_its_exit_code() -> None:
    """Only a closed stdout exits 0: an exit raised while handling a broken
    pipe to some other process (a hook worker) is a real failure."""
    app = App(name="test")

    @app.default
    def worker_died() -> None:
        try:
            raise BrokenPipeError(32, "Broken pipe")
        except BrokenPipeError:
            raise SystemExit(1) from None

    result = CliInvoker().invoke(app, [])
    assert result.exit_code == 1


def _run_with_closed_stdout(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``untaped <argv>`` with stdout a pipe whose reader is already gone."""
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        return subprocess.run(
            [sys.executable, "-c", "from untaped.runtime.bootstrap import main; main()", *argv],
            stdout=write_end,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    finally:
        os.close(write_end)


def test_help_into_closed_pipe_exits_zero_quietly() -> None:
    """``untaped --help | head`` must not exit 1 through Rich's broken-pipe hook."""
    proc = _run_with_closed_stdout(["--help"])
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_data_command_into_closed_pipe_exits_zero_quietly() -> None:
    proc = _run_with_closed_stdout(["config", "list", "-f", "json"])
    assert proc.returncode == 0
    assert proc.stderr == ""


# ---- _format_error JSON body extraction ------------------------------------


def _http_error_app(body: str) -> App:
    app = App(name="test")

    @app.default
    def boom() -> None:
        with report_errors():
            raise HttpError(
                "HTTP 401 for https://api.example.test/x",
                status_code=401,
                url="https://api.example.test/x",
                body=body,
            )

    return app


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"message":"Bad credentials"}', "Bad credentials"),
        ('{"error":"invalid_token"}', "invalid_token"),
        ('{"detail":"Not found"}', "Not found"),
        ('{"errors":[{"message":"field is required"}]}', "field is required"),
    ],
    ids=["message", "error", "detail", "errors-list"],
)
def test_format_error_surfaces_known_json_message(body: str, expected: str) -> None:
    result = CliInvoker().invoke(_http_error_app(body), [])
    output = result.output or result.stderr
    assert result.exit_code == 4  # a rejected token: the environment needs fixing
    assert "error: HTTP 401 for https://api.example.test/x" in output
    assert f"— {expected}" in output
    assert "response:" not in output


@pytest.mark.parametrize(
    "body",
    # Valid JSON with no known message key (Jira's ``errorMessages``) stays raw too.
    ["<html>nope</html>", '{"errorMessages":["boom"]}'],
)
def test_format_error_falls_back_to_raw_body(body: str) -> None:
    result = CliInvoker().invoke(_http_error_app(body), [])
    assert f"response: {body}" in (result.output or result.stderr)


def test_format_error_keeps_raw_body_under_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("untaped.sdk.diagnostic.is_verbose", lambda: True)
    result = CliInvoker().invoke(_http_error_app('{"message":"Bad credentials"}'), [])
    output = result.output or result.stderr
    assert "— Bad credentials" in output
    assert "response:" in output


@pytest.mark.parametrize(
    "message", ["connection failed", "connection failed for https://api.example.test/x"]
)
def test_format_error_names_the_transport_url_once(message: str) -> None:
    app = App(name="test")

    @app.default
    def boom() -> None:
        with report_errors():
            raise HttpTransportError(message, url="https://api.example.test/x")

    result = CliInvoker().invoke(app, [])
    output = result.output or result.stderr
    assert result.exit_code == 5  # unavailable: retry later
    assert "error: connection failed for https://api.example.test/x" in output
    assert output.count("https://api.example.test/x") == 1
