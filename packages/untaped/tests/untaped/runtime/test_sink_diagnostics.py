"""Stderr diagnostics: JSON Lines under structured formats, and the run's exit code.

``--format json|yaml|pipe`` (or ``UNTAPED_DIAGNOSTICS=json``) turns every
stderr diagnostic into one JSON object per line; ``UNTAPED_DIAGNOSTICS=text``
keeps text. stdout never changes. A run exits with the most severe failure it
saw (``130 > 2 > 4 > 5 > 1 > 3 > 0``).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from cyclopts import App
from pydantic import BaseModel

from core_test_support.commands.support import make_plugin
from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.sink import Diagnostics
from untaped.sdk import Command, Output
from untaped.sdk import _cli as cli
from untaped.sdk._cli import emit, render_rows, report_declined
from untaped.sdk._theme import ThemeSpec
from untaped.sdk._ui import Ui
from untaped.sdk.errors import (
    ConfigError,
    Failed,
    HttpStatusError,
    HttpTransportError,
    OperationCancelledError,
    UntapedError,
)
from untaped.sdk.records import Failure, OutcomeRecord
from untaped.sdk.testing import CliInvoker, CliResult, invoke_cli, plugin_candidate


class Row(BaseModel):
    id: int


def _run(body: Callable[[], object], args: list[str], *, fmt: str = "table") -> CliResult:
    """``untaped demo run *args``: a command that calls ``body``, then prints one row."""

    class Run(Command):
        """Run it."""

        output: Output[Row] = Output(format=fmt)  # type: ignore[arg-type]

        def run(self) -> Iterator[Row]:
            body()
            yield Row(id=1)

    root = build_root_app(candidates=[plugin_candidate(make_plugin("demo", commands=(Run,)))])
    return CliInvoker().invoke(root.meta, ["demo", "run", *args])


def _rejected_token() -> None:
    raise ConfigError(
        "AWX rejected the token (HTTP 401)",
        category="auth",
        system="awx",
        hint="run `untaped config set awx.token --prompt`",
        details={"status": 401, "url": "https://aap/api/v2/me/"},
    )


def _lines(stderr: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stderr.splitlines()]


@pytest.mark.parametrize("fmt", ["json", "yaml", "pipe"])
def test_structured_formats_write_json_error_lines(fmt: str) -> None:
    result = _run(_rejected_token, ["--format", fmt])

    assert result.exit_code == 4
    assert result.stdout == ""
    assert _lines(result.stderr) == [
        {
            "level": "error",
            "kind": "untaped.failure",
            "message": "AWX rejected the token (HTTP 401)",
            "category": "auth",
            "system": "awx",
            "retryable": False,
            "hint": "run `untaped config set awx.token --prompt`",
            "exit_code": 4,
            "details": {"status": 401, "url": "https://aap/api/v2/me/"},
        }
    ]


def test_table_format_keeps_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNTAPED_FORMAT", raising=False)
    result = _run(_rejected_token, ["--format", "table"])

    assert result.exit_code == 4
    assert result.stderr == (
        "error: AWX rejected the token (HTTP 401)\n"
        "hint: run `untaped config set awx.token --prompt`\n"
    )


def test_a_command_s_own_structured_default_keeps_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNTAPED_FORMAT", raising=False)

    assert _run(_rejected_token, [], fmt="yaml").stderr.startswith("error: AWX rejected the token")
    yaml = _run(_rejected_token, ["--format", "yaml"], fmt="yaml")
    assert _lines(yaml.stderr)[0]["category"] == "auth"


def test_the_user_s_default_format_selects_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "json")

    result = _run(_rejected_token, [])

    assert _lines(result.stderr)[0]["system"] == "awx"


def test_the_environment_variable_forces_either_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "text")
    text = _run(_rejected_token, ["--format", "json"])
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    structured = _run(_rejected_token, ["--format", "table"])

    assert text.stderr.startswith("error: AWX rejected the token")
    assert _lines(structured.stderr)[0]["category"] == "auth"


def test_warnings_hints_and_notes_are_json_lines_and_stdout_is_unchanged() -> None:
    def body() -> None:
        diag = Diagnostics()
        diag.warning("--parallel 64 clamped to 16")
        diag.warning("stale skill")
        diag.hint("run `untaped skills install`")
        diag.info("sync: 2 cloned", unmuted=True)

    plain = _run(lambda: None, ["--format", "json"])
    result = _run(body, ["--format", "json"])

    assert result.exit_code == 0
    assert result.stdout == plain.stdout == '[{"id": 1}]\n'
    assert _lines(result.stderr) == [
        {"level": "warning", "kind": "untaped.message", "message": "--parallel 64 clamped to 16"},
        {"level": "warning", "kind": "untaped.message", "message": "stale skill"},
        {"level": "hint", "kind": "untaped.message", "message": "run `untaped skills install`"},
        {"level": "info", "kind": "untaped.message", "message": "sync: 2 cloned"},
    ]


def test_per_item_errors_name_their_item() -> None:
    def fail(name: str) -> None:
        raise HttpStatusError("HTTP 404", status_code=404, url="https://h/x", system="jira")

    def body() -> None:
        try:
            fail("ABC-1")
        except UntapedError as exc:
            Diagnostics().failure(exc, item="ABC-1")

    result = _run(body, ["--format", "json"])

    (line,) = _lines(result.stderr)
    assert (line["item"], line["category"], line["system"]) == ("ABC-1", "not_found", "jira")
    assert result.exit_code == 1


def test_the_most_severe_failure_in_the_run_selects_the_exit_code() -> None:
    def fail(name: str) -> None:
        category = {"a": "not_found", "b": "unavailable", "c": "invalid"}[name]
        raise UntapedError(f"{name} failed", category=category)

    def body() -> None:
        for name in ("a", "b", "c"):
            try:
                fail(name)
            except UntapedError as exc:
                Diagnostics().failure(exc, item=name)

    assert _run(body, []).exit_code == 5


def test_an_environment_failure_outranks_a_later_error() -> None:
    def body() -> None:
        Diagnostics().failure(_rejected_token(), item="x")
        raise UntapedError("x not found", category="not_found")

    assert _run(body, []).exit_code == 4


def test_usage_errors_are_json_lines_under_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")

    result = _run(lambda: None, ["--unknown"])

    (line,) = _lines(result.stderr)
    assert (line["category"], line["exit_code"], result.exit_code) == ("usage", 2, 2)


@pytest.mark.parametrize(
    "args", [["--format", "json", "--bogus"], ["--format=pipe", "--bogus"], ["-f", "yaml", "-x"]]
)
def test_a_parse_error_follows_the_requested_format(args: list[str]) -> None:
    result = _run(lambda: None, args)

    (line,) = _lines(result.stderr)
    assert (line["category"], result.exit_code) == ("usage", 2)


def test_a_parse_error_follows_the_user_s_default_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "json")

    result = _run(lambda: None, ["--bogus"])

    assert _lines(result.stderr)[0]["category"] == "usage"


def test_url_passwords_never_leave_the_process() -> None:
    def body() -> None:
        raise HttpStatusError(
            "HTTP 401 for https://bot:s3cret@aap.example/api/v2/me/",
            status_code=401,
            url="https://bot:s3cret@aap.example/api/v2/me/",
            system="awx",
        )

    structured = _run(body, ["--format", "json"])
    text = _run(body, ["--format", "table"])

    assert "s3cret" not in structured.stderr + text.stderr
    assert (
        _lines(structured.stderr)[0]["details"]["url"] == "https://bot:***@aap.example/api/v2/me/"
    )
    info = Failure.from_exception(HttpTransportError("down", url="http://u:pw@h/x"))
    assert "pw" not in info.message


# ---- the derived exit code ---------------------------------------------------


def test_a_failed_command_exits_with_its_most_severe_counted_failure() -> None:
    def body() -> None:
        Diagnostics().failure(ConfigError("token rejected", category="auth"))
        raise Failed()

    assert _run(body, []).exit_code == 4


def test_a_command_that_emits_a_failure_and_returns_still_fails() -> None:
    def body() -> None:
        Diagnostics().failure(HttpTransportError("down", system="awx"))

    result = _run(body, [])

    assert result.exit_code == 5
    assert result.stdout  # the rows still print


def test_a_warning_alone_keeps_exit_0() -> None:
    assert _run(lambda: Diagnostics().warning("careful"), []).exit_code == 0


class _Outcome(OutcomeRecord):
    name: str


@pytest.mark.parametrize("as_dict", [False, True])
def test_an_emitted_failed_row_sets_exit_1(as_dict: bool) -> None:
    error = Failure.from_exception(UntapedError("broke"))
    row = _Outcome(name="a", action="failed", error=error)
    rows: list[Any] = [row.model_dump(mode="json") if as_dict else row]

    app = App(name="demo")

    @app.default
    def run() -> None:
        emit(rows, fmt="json")

    assert invoke_cli(app, []).exit_code == 1


def test_a_failed_row_counts_in_a_table_list_view(monkeypatch: pytest.MonkeyPatch) -> None:
    error = Failure.from_exception(UntapedError("broke"))
    row = _Outcome(name="a", action="failed", error=error).model_dump(mode="json")
    theme = ThemeSpec(collection_view="list")
    monkeypatch.setattr(cli, "build_terminal", lambda **_: Ui(theme=theme))
    app = App(name="demo")

    @app.default
    def run() -> None:
        render_rows([row], fmt="table")

    assert invoke_cli(app, []).exit_code == 1


def test_a_predicate_hit_keeps_exit_3() -> None:
    def body() -> None:
        raise SystemExit(3)

    assert _run(body, []).exit_code == 3


def test_a_declined_run_exits_1_with_its_bare_line() -> None:
    def body() -> None:
        report_declined(OperationCancelledError("cancelled; no changes made"))
        raise SystemExit(1)

    result = _run(body, [])

    assert (result.exit_code, result.stderr) == (1, "cancelled; no changes made\n")


@pytest.mark.parametrize("structured", [False, True])
def test_a_plain_error_message_is_a_counted_failure(structured: bool) -> None:
    result = _run(
        lambda: Diagnostics().failure(Failure.plain("it broke")),
        ["--format", "json"] if structured else [],
    )

    assert result.exit_code == 1
    if structured:
        [line] = _lines(result.stderr)
        assert (line["kind"], line["category"], line["message"]) == (
            "untaped.failure",
            "failed",
            "it broke",
        )
    else:
        assert result.stderr == "error: it broke\n"
    assert [type(each).__name__ for each in result.diagnostics] == ["Failure"]
