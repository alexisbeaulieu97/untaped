"""Stderr diagnostics: JSON Lines under structured formats, and the run's exit code.

``--format json|yaml|pipe`` (or ``UNTAPED_DIAGNOSTICS=json``) turns every
stderr diagnostic into one JSON object per line; ``UNTAPED_DIAGNOSTICS=text``
keeps text. stdout never changes. A run exits with the most severe failure it
saw (``130 > 2 > 4 > 5 > 1 > 3 > 0``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from cyclopts import App

from untaped.batch import finish
from untaped.cli import FormatOption, apply_default_format, echo, emit, report_errors, resolve_each
from untaped.errors import ConfigError, HttpStatusError, UntapedError
from untaped.messages import hint
from untaped.testing import invoke_cli
from untaped.ui import UiContext


def _app(body: Any) -> App:
    app = App(name="demo", config=(apply_default_format,))

    @app.default
    def run(*, fmt: FormatOption = "table") -> None:
        with report_errors():
            body()
            emit([{"id": 1}], fmt=fmt)

    return app


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
    result = invoke_cli(_app(_rejected_token), ["--format", fmt])

    assert result.exit_code == 4
    assert result.stdout == ""
    assert _lines(result.stderr) == [
        {
            "level": "error",
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
    result = invoke_cli(_app(_rejected_token), ["--format", "table"])

    assert result.exit_code == 4
    assert result.stderr == (
        "error: AWX rejected the token (HTTP 401)\n"
        "hint: run `untaped config set awx.token --prompt`\n"
    )


def test_a_command_s_own_structured_default_keeps_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNTAPED_FORMAT", raising=False)
    app = App(name="demo", config=(apply_default_format,))

    @app.default
    def export(*, fmt: FormatOption = "yaml") -> None:
        with report_errors():
            _rejected_token()

    assert invoke_cli(app, []).stderr.startswith("error: AWX rejected the token")
    assert _lines(invoke_cli(app, ["--format", "yaml"]).stderr)[0]["category"] == "auth"


def test_the_user_s_default_format_selects_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "json")

    result = invoke_cli(_app(_rejected_token), [])

    assert _lines(result.stderr)[0]["system"] == "awx"


def test_the_environment_variable_forces_either_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "text")
    text = invoke_cli(_app(_rejected_token), ["--format", "json"])
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    structured = invoke_cli(_app(_rejected_token), ["--format", "table"])

    assert text.stderr.startswith("error: AWX rejected the token")
    assert _lines(structured.stderr)[0]["category"] == "auth"


def test_warnings_hints_and_notes_are_json_lines_and_stdout_is_unchanged() -> None:
    def body() -> None:
        UiContext().message("warning", "--parallel 64 clamped to 16")
        echo("warning: stale skill", err=True)
        echo(hint("skills install"), err=True)
        echo("sync: 2 cloned", err=True)

    plain = invoke_cli(_app(lambda: None), ["--format", "json"])
    result = invoke_cli(_app(body), ["--format", "json"])

    assert result.exit_code == 0
    assert result.stdout == plain.stdout == '[{"id": 1}]\n'
    assert _lines(result.stderr) == [
        {"level": "warning", "message": "--parallel 64 clamped to 16"},
        {"level": "warning", "message": "stale skill"},
        {"level": "hint", "message": "run `untaped skills install`"},
        {"level": "info", "message": "sync: 2 cloned"},
    ]


def test_per_item_errors_name_their_item() -> None:
    def fail(name: str) -> None:
        raise HttpStatusError("HTTP 404", status_code=404, url="https://h/x", system="jira")

    def body() -> None:
        _, failed = resolve_each(["ABC-1"], fail)
        finish(failed)

    result = invoke_cli(_app(body), ["--format", "json"])

    (line,) = _lines(result.stderr)
    assert (line["item"], line["category"], line["system"]) == ("ABC-1", "not_found", "jira")
    assert result.exit_code == 1


def test_the_most_severe_failure_in_the_run_selects_the_exit_code() -> None:
    def fail(name: str) -> None:
        category = {"a": "not_found", "b": "unavailable", "c": "invalid"}[name]
        raise UntapedError(f"{name} failed", category=category)

    def body() -> None:
        _, failed = resolve_each(["a", "b", "c"], fail)
        finish(failed)

    assert invoke_cli(_app(body), []).exit_code == 5


def test_an_environment_failure_outranks_a_later_error() -> None:
    def body() -> None:
        resolve_each(["x"], lambda _: _rejected_token())
        raise UntapedError("x not found", category="not_found")

    assert invoke_cli(_app(body), []).exit_code == 4


def test_usage_errors_are_json_lines_under_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")

    result = invoke_cli(_app(lambda: None), ["--unknown"])

    (line,) = _lines(result.stderr)
    assert (line["category"], line["exit_code"], result.exit_code) == ("usage", 2, 2)
