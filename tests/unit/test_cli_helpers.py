import json
import os
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
import yaml
from cyclopts import App
from pydantic import BaseModel

from untaped.capability_api import (
    HttpError,
    HttpTransportError,
    UntapedError,
    clamp_parallel,
    create_app,
    emit,
    parse_json_pairs,
    parse_kv_pairs,
    render_rows,
    report_errors,
    resolve_each,
)
from untaped.cli import run_cyclopts_app
from untaped.settings import get_settings
from untaped.testing import CliInvoker


def test_clean_message_for_untaped_error() -> None:
    app = create_app(name="test")

    @app.default
    def boom() -> None:
        with report_errors():
            raise UntapedError("something went wrong")

    result = CliInvoker().invoke(app, [])
    assert result.exit_code == 1
    assert "error: something went wrong" in (result.output or result.stderr)


def test_passes_through_non_untaped_exception() -> None:
    """Non-UntapedError exceptions should not be swallowed — they're bugs."""
    app = create_app(name="test")

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
    app = create_app(name="test")

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
    app = create_app(name="test")

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
    app = create_app(name="test")

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
            [sys.executable, "-c", "from untaped.bootstrap import main; main()", *argv],
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


# ---- parse_kv_pairs / parse_json_pairs -------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (None, {}),
        ([], {}),
        (["a=1", "b=2"], {"a": "1", "b": "2"}),
        # Splits on the first ``=`` so values with literal ``=`` survive intact.
        (["filter=foo=bar=baz"], {"filter": "foo=bar=baz"}),
        (["k="], {"k": ""}),
        (["  key  =val"], {"key": "val"}),
        (["k=first", "k=second"], {"k": "second"}),
    ],
)
def test_parse_kv_pairs(values: list[str] | None, expected: dict[str, str]) -> None:
    assert parse_kv_pairs(values, flag="--filter") == expected


@pytest.mark.parametrize(
    ("parse", "entry"),
    [
        (parse_kv_pairs, "bogus"),
        (parse_kv_pairs, "=value"),
        (parse_kv_pairs, "   =value"),
        (parse_json_pairs, "notapair"),
        (parse_json_pairs, "k={broken"),
    ],
)
def test_malformed_pairs_are_usage_errors_naming_the_flag(
    parse: Any, entry: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        parse([entry], flag="--custom")
    assert exc.value.code == 2
    assert "--custom" in capsys.readouterr().err


def test_parse_json_pairs_decodes_values() -> None:
    assert parse_json_pairs(None, flag="--json-field") == {}
    out = parse_json_pairs(['labels=["a","b"]', "count=3", 'name="x"'], flag="--json-field")
    assert out == {"labels": ["a", "b"], "count": 3, "name": "x"}


# ---- resolve_each --------------------------------------------------------


@pytest.mark.parametrize(("ids", "expected"), [([], []), (["a", "b"], ["A", "B"])])
def test_resolve_each_without_failures(
    ids: list[str], expected: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert resolve_each(ids, str.upper) == (expected, False)
    assert capsys.readouterr().err == ""


def test_resolve_each_collects_successes_and_echoes_per_id_untaped_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fn(n: str) -> str:
        if n == "bad":
            raise UntapedError("not found")
        return n.upper()

    results, any_failed = resolve_each(["a", "bad", "c"], fn)
    assert results == ["A", "C"]
    assert any_failed is True
    assert "error: bad: not found" in capsys.readouterr().err


def test_resolve_each_surfaces_json_api_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fn(_id: str) -> str:
        raise HttpError(
            "HTTP 403 for https://api.example.test/secure",
            status_code=403,
            url="https://api.example.test/secure",
            body='{"detail":"missing permission"}',
        )

    results, any_failed = resolve_each(["secure"], fn)

    err = capsys.readouterr().err
    assert results == []
    assert any_failed is True
    assert "error: secure: HTTP 403 for https://api.example.test/secure" in err
    assert "— missing permission" in err
    assert "response:" not in err


def test_resolve_each_propagates_non_untaped_exceptions() -> None:
    """Non-UntapedError exceptions are bugs and must surface, not be swallowed."""

    def fn(n: str) -> str:
        raise ValueError("bug")

    with pytest.raises(ValueError, match="bug"):
        resolve_each(["x"], fn)


# ---- clamp_parallel ------------------------------------------------------


@pytest.mark.parametrize("requested", [4, 8, 0, -3])
def test_clamp_parallel_passes_through_up_to_cap(
    requested: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """``requested == cap`` is inclusive; ``< 1`` is caller policy, left untouched."""
    assert clamp_parallel(requested, cap=8, policy="2 * os.cpu_count()") == requested
    assert capsys.readouterr().err == ""


def test_clamp_parallel_caps_above_with_warning_naming_the_policy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert clamp_parallel(100, cap=8, policy="httpx.Limits.max_connections=8") == 8
    err = capsys.readouterr().err
    assert "warning: --parallel 100 clamped to 8 (httpx.Limits.max_connections=8)" in err


# ---- render_rows -----------------------------------------------------------


def test_render_rows_structured_formats_ignore_theme(_isolated_config: Path) -> None:
    """json/raw output must stay byte-stable no matter the configured theme."""
    _isolated_config.write_text("ui:\n  theme: dark\n")
    get_settings.cache_clear()

    rows: list[dict[str, object]] = [{"name": "alpha", "value": "1"}]
    assert json.loads(render_rows(rows, fmt="json")) == [{"name": "alpha", "value": "1"}]
    raw = render_rows(rows, fmt="raw", columns=["name"])
    assert raw == "alpha"
    assert "\x1b[" not in raw


def test_render_rows_pipe_tags_each_record_with_kind(_isolated_config: Path) -> None:
    rows: list[dict[str, object]] = [{"full_name": "a/b"}, {"full_name": "c/d"}]
    out = render_rows(rows, fmt="pipe", kind="github.repo")
    assert [json.loads(line) for line in out.splitlines()] == [
        {"untaped": "1", "kind": "github.repo", "record": {"full_name": "a/b"}},
        {"untaped": "1", "kind": "github.repo", "record": {"full_name": "c/d"}},
    ]


def test_render_rows_pipe_kind_defaults_to_null(_isolated_config: Path) -> None:
    out = render_rows([{"x": 1}], fmt="pipe")
    assert json.loads(out)["kind"] is None


@pytest.mark.parametrize(
    "kind",
    [
        "github.code_hit",
        "awx.apply_outcome",
        "profile.profile",
        "jira.issue.summary",
        "health.metric_source",
    ],
)
def test_valid_kinds_are_accepted(kind: str) -> None:
    assert render_rows([{"id": 1}], fmt="pipe", kind=kind).startswith("{")


@pytest.mark.parametrize(
    "kind",
    [
        "github.codehit-x",
        "awx.apply-outcome",
        "Github.code_hit",
        "code_hit",
        "github.",
        "github.code_hit.extra",
        "github.code_hit.summary.x",
    ],
)
def test_invalid_kinds_raise_value_error(kind: str) -> None:
    with pytest.raises(ValueError, match="invalid pipe kind"):
        render_rows([{"id": 1}], fmt="pipe", kind=kind)


def test_summary_kind_is_reserved_as_suffix_only() -> None:
    with pytest.raises(ValueError, match="<tool>\\.<noun>\\.summary"):
        render_rows([{"id": 1}], fmt="pipe", kind="github.summary")


def test_emit_validates_kind_for_single_records() -> None:
    with pytest.raises(ValueError, match="invalid pipe kind"):
        emit({"id": 1}, fmt="json", kind="bad-kind")


# ---- emit ------------------------------------------------------------------


class _Widget(BaseModel):
    name: str
    value: int


@pytest.mark.parametrize("record", [_Widget(name="alpha", value=1), {"name": "alpha", "value": 1}])
def test_emit_single_record_json_is_bare_object(
    record: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """A single entity (model or bare mapping) under ``--format json`` is a
    bare object, not a 1-element array."""
    emit(record, fmt="json")  # type: ignore[arg-type]
    assert json.loads(capsys.readouterr().out) == {"name": "alpha", "value": 1}


def test_emit_sequence_json_is_array(capsys: pytest.CaptureFixture[str]) -> None:
    """A sequence stays a JSON array — collection semantics."""
    emit([_Widget(name="alpha", value=1), _Widget(name="beta", value=2)], fmt="json")
    assert json.loads(capsys.readouterr().out) == [
        {"name": "alpha", "value": 1},
        {"name": "beta", "value": 2},
    ]


class _Color(Enum):
    RED = "red"


class _Located(BaseModel):
    path: Path
    color: _Color


@pytest.mark.parametrize("fmt", ["yaml", "json", "pipe"])
def test_emit_model_with_path_and_enum_uses_json_values(
    capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    emit([_Located(path=Path("/tmp/x"), color=_Color.RED)], fmt=fmt)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "/tmp/x" in out
    assert "red" in out
    assert "_Color" not in out


def test_emit_yaml_mapping_with_unknown_values_falls_back_to_str(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit({"path": Path("/tmp/x"), "color": _Color.RED, "tags": [Path("a")]}, fmt="yaml")
    assert yaml.safe_load(capsys.readouterr().out) == {
        "path": "/tmp/x",
        "color": "red",
        "tags": ["a"],
    }


def test_emit_single_pipe_emits_one_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    emit(_Widget(name="alpha", value=1), fmt="pipe", kind="demo.widget")
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "untaped": "1",
        "kind": "demo.widget",
        "record": {"name": "alpha", "value": 1},
    }


def test_emit_single_model_table_renders_vertical_detail(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Default theme detail_view is ``list`` — a vertical key:value block, not a wide table."""
    emit(_Widget(name="alpha", value=1), fmt="table")
    out = capsys.readouterr().out
    assert "name" in out
    assert "alpha" in out


def test_emit_empty_sequence_table_emits_no_spurious_stdout(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([], fmt="table", empty="Nothing here.")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Nothing here." in captured.err


# ---- _format_error JSON body extraction ------------------------------------


def _http_error_app(body: str) -> App:
    app = create_app(name="test")

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
    assert result.exit_code == 1
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
    monkeypatch.setattr("untaped.cli.is_verbose", lambda: True)
    result = CliInvoker().invoke(_http_error_app('{"message":"Bad credentials"}'), [])
    output = result.output or result.stderr
    assert "— Bad credentials" in output
    assert "response:" in output


@pytest.mark.parametrize(
    "message", ["connection failed", "connection failed for https://api.example.test/x"]
)
def test_format_error_names_the_transport_url_once(message: str) -> None:
    app = create_app(name="test")

    @app.default
    def boom() -> None:
        with report_errors():
            raise HttpTransportError(message, url="https://api.example.test/x")

    result = CliInvoker().invoke(app, [])
    output = result.output or result.stderr
    assert result.exit_code == 1
    assert "error: connection failed for https://api.example.test/x" in output
    assert output.count("https://api.example.test/x") == 1


# ---- --columns ? discoverability -------------------------------------------


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("fmt", ["table", "json", "raw", "yaml"])
def test_emit_unknown_model_column_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], fmt: str, single: bool
) -> None:
    widget = _Widget(name="a", value=1)
    with pytest.raises(SystemExit) as excinfo:
        emit(widget if single else [widget], fmt=fmt, columns=["name", "nmae"])  # type: ignore[arg-type]
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unknown column 'nmae'" in captured.err
    assert "name, value" in captured.err


def test_emit_unknown_mapping_column_warns_but_renders(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mapping rows can be sparse (APIs omit fields), so an absent name only warns."""
    emit([{"name": "a", "value": 1}], fmt="raw", columns=["name", "nmae"])
    captured = capsys.readouterr()
    assert captured.out == "a\t\n"
    assert "warning: unknown column 'nmae'; valid columns: name, value" in captured.err


def test_emit_column_names_containing_dots_match_whole_keys(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit([{"has-file:release.txt": True}], fmt="raw", columns=["has-file:release.txt"])
    captured = capsys.readouterr()
    assert captured.err == ""


@pytest.mark.parametrize(
    ("columns", "expected"),
    [(["name,value"], "a\t1\n"), (["name, value", "extra"], "a\t1\t2\n")],
    ids=["comma-list", "mixed-with-repeats"],
)
def test_emit_accepts_comma_separated_columns(
    columns: list[str], expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([{"name": "a", "value": 1, "extra": 2}], fmt="raw", columns=columns)
    assert capsys.readouterr().out == expected


def test_emit_dotted_columns_validate_their_first_segment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit([{"meta": {"id": 7}}], fmt="raw", columns=["meta.id"])
    assert capsys.readouterr() == ("7\n", "")
    emit([{"meta": {"id": 7}}], fmt="raw", columns=["mta.id"])
    assert "unknown column 'mta.id'" in capsys.readouterr().err


def test_emit_unknown_column_on_empty_result_is_not_an_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit([], fmt="json", columns=["anything"])
    assert capsys.readouterr().out == "[]\n"


@pytest.mark.parametrize(
    "render",
    [
        lambda: emit(_Widget(name="a", value=1), fmt="table", columns=["?"]),
        lambda: render_rows([{"name": "a", "value": 1}], fmt="table", columns=["?"]),
    ],
    ids=["emit-model", "render-rows-mapping"],
)
def test_columns_question_mark_lists_columns_instead_of_rendering(
    render: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert not render()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "name" in captured.err
    assert "value" in captured.err
