import json
import sys
from pathlib import Path

import pytest
from cyclopts import App
from pydantic import BaseModel

from untaped import (
    HttpError,
    HttpTransportError,
    UntapedError,
    clamp_parallel,
    create_app,
    emit,
    get_settings,
    parse_json_pairs,
    parse_kv_pairs,
    render_rows,
    report_errors,
    resolve_each,
)
from untaped.cli import run_cyclopts_app
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


def test_report_errors_surfaces_json_api_message() -> None:
    """A JSON error body's human message is surfaced inline; the raw body is
    not dumped (that only happens for unparseable bodies, or under --verbose)."""
    app = create_app(name="test")

    @app.default
    def boom() -> None:
        with report_errors():
            raise HttpError(
                "HTTP 403 for https://api.github.com/repos/acme/private",
                status_code=403,
                url="https://api.github.com/repos/acme/private",
                body='{"message":"Resource not accessible by personal access token"}',
            )

    result = CliInvoker().invoke(app, [])

    assert result.exit_code == 1
    output = result.output or result.stderr
    assert "error: HTTP 403 for https://api.github.com/repos/acme/private" in output
    assert "— Resource not accessible by personal access token" in output
    assert "response:" not in output


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
    it must convert to a clean ``SystemExit(1)``, not leak as a traceback."""
    app = create_app(name="test")

    @app.default
    def boom() -> None:
        raise BrokenPipeError(32, "Broken pipe")

    result = CliInvoker().invoke(app, [])
    assert result.exit_code == 1
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
    assert exc.value.code == 1


# ---- parse_kv_pairs ------------------------------------------------------


def test_parse_kv_pairs_returns_empty_dict_for_none() -> None:
    assert parse_kv_pairs(None, flag="--filter") == {}


def test_parse_kv_pairs_returns_empty_dict_for_empty_iterable() -> None:
    assert parse_kv_pairs([], flag="--filter") == {}


def test_parse_kv_pairs_basic_kv() -> None:
    assert parse_kv_pairs(["k=v"], flag="--filter") == {"k": "v"}


def test_parse_kv_pairs_multiple_entries() -> None:
    assert parse_kv_pairs(["a=1", "b=2"], flag="--filter") == {"a": "1", "b": "2"}


def test_parse_kv_pairs_value_can_contain_equals() -> None:
    """Splits on the first ``=`` so values with literal ``=`` survive intact."""
    assert parse_kv_pairs(["filter=foo=bar=baz"], flag="--filter") == {"filter": "foo=bar=baz"}


def test_parse_kv_pairs_empty_value_is_allowed() -> None:
    assert parse_kv_pairs(["k="], flag="--filter") == {"k": ""}


def test_parse_kv_pairs_strips_key_whitespace() -> None:
    assert parse_kv_pairs(["  key  =val"], flag="--var") == {"key": "val"}


def test_parse_kv_pairs_rejects_missing_equals() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_kv_pairs(["bogus"], flag="--filter")
    assert exc.value.code == 2


def test_parse_kv_pairs_rejects_empty_key() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_kv_pairs(["=value"], flag="--filter")
    assert exc.value.code == 2


def test_parse_kv_pairs_rejects_whitespace_only_key() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_kv_pairs(["   =value"], flag="--var")
    assert exc.value.code == 2


def test_parse_kv_pairs_error_uses_provided_flag_name() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_kv_pairs(["bogus"], flag="--custom")
    assert exc.value.code == 2


def test_parse_kv_pairs_later_entries_overwrite_earlier() -> None:
    assert parse_kv_pairs(["k=first", "k=second"], flag="--filter") == {"k": "second"}


# ---- parse_json_pairs -----------------------------------------------------


def test_parse_json_pairs_decodes_values() -> None:
    out = parse_json_pairs(['labels=["a","b"]', "count=3", 'name="x"'], flag="--json-field")
    assert out == {"labels": ["a", "b"], "count": 3, "name": "x"}


def test_parse_json_pairs_none_is_empty() -> None:
    assert parse_json_pairs(None, flag="--json-field") == {}


def test_parse_json_pairs_rejects_missing_equals() -> None:
    with pytest.raises(SystemExit):
        parse_json_pairs(["notapair"], flag="--json-field")


def test_parse_json_pairs_rejects_invalid_json() -> None:
    with pytest.raises(SystemExit):
        parse_json_pairs(["k={broken"], flag="--json-field")


# ---- resolve_each --------------------------------------------------------


def test_resolve_each_with_empty_ids_returns_empty_and_no_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty input must not call ``fn`` and must report no failures."""
    calls: list[str] = []
    results, any_failed = resolve_each([], lambda n: calls.append(n) or n)
    assert results == []
    assert any_failed is False
    assert calls == []
    assert capsys.readouterr().err == ""


def test_resolve_each_returns_results_when_all_succeed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results, any_failed = resolve_each(["a", "b", "c"], lambda n: n.upper())
    assert results == ["A", "B", "C"]
    assert any_failed is False
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


def test_clamp_parallel_returns_input_when_below_cap(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Below-cap values pass through untouched and emit no warning."""
    assert clamp_parallel(4, cap=8, policy="2 * os.cpu_count()") == 4
    assert capsys.readouterr().err == ""


def test_clamp_parallel_returns_input_at_cap_inclusive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``requested == cap`` is *inclusive* (no warning, no clamp); the
    helper uses ``<= cap`` not ``< cap`` so the boundary is honoured."""
    assert clamp_parallel(8, cap=8, policy="2 * os.cpu_count()") == 8
    assert capsys.readouterr().err == ""


def test_clamp_parallel_caps_above_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Above-cap returns ``cap`` and emits a stderr warning naming the policy."""
    assert clamp_parallel(100, cap=8, policy="2 * os.cpu_count()") == 8
    err = capsys.readouterr().err
    assert "warning: --parallel 100 clamped to 8" in err
    assert "(2 * os.cpu_count())" in err


def test_clamp_parallel_policy_string_appears_in_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Caller-supplied ``policy`` text appears verbatim in the parens —
    callers control the rationale (httpx pool, cpu_count, ...)."""
    clamp_parallel(50, cap=10, policy="httpx.Limits.max_connections=10")
    err = capsys.readouterr().err
    assert "clamped to 10 (httpx.Limits.max_connections=10)" in err


def test_clamp_parallel_does_not_handle_below_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``< 1`` policy is caller-specific (silent coerce vs BadParameter)
    so the helper deliberately doesn't clamp at the lower bound — it
    returns the input untouched, no warning."""
    assert clamp_parallel(0, cap=8, policy="2 * os.cpu_count()") == 0
    assert clamp_parallel(-3, cap=8, policy="2 * os.cpu_count()") == -3
    assert capsys.readouterr().err == ""


# ---- render_rows -----------------------------------------------------------


def test_render_rows_table_contains_cells(_isolated_config: Path) -> None:
    rendered = render_rows([{"name": "alpha", "value": "1"}], fmt="table")
    assert "alpha" in rendered
    assert "name" in rendered


def test_render_rows_empty_table_emits_hint_to_stderr(
    _isolated_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered = render_rows([], fmt="table", empty="No plugins installed.")
    captured = capsys.readouterr()
    assert rendered == ""
    assert captured.out == ""
    assert "No plugins installed." in captured.err


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
        "github.summary",
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


def test_kind_none_is_always_accepted() -> None:
    assert render_rows([{"id": 1}], fmt="json", kind=None)


# ---- emit ------------------------------------------------------------------


class _Widget(BaseModel):
    name: str
    value: int


def test_emit_single_model_json_is_bare_object(capsys: pytest.CaptureFixture[str]) -> None:
    """A single entity under ``--format json`` is a bare object, not a 1-element array."""
    emit(_Widget(name="alpha", value=1), fmt="json")
    assert json.loads(capsys.readouterr().out) == {"name": "alpha", "value": 1}


def test_emit_sequence_json_is_array(capsys: pytest.CaptureFixture[str]) -> None:
    """A sequence stays a JSON array — collection semantics."""
    emit([_Widget(name="alpha", value=1), _Widget(name="beta", value=2)], fmt="json")
    assert json.loads(capsys.readouterr().out) == [
        {"name": "alpha", "value": 1},
        {"name": "beta", "value": 2},
    ]


def test_emit_accepts_a_single_mapping(capsys: pytest.CaptureFixture[str]) -> None:
    """A bare dict is treated as one record (detail), not iterated as a sequence."""
    emit({"name": "alpha", "value": 1}, fmt="json")
    assert json.loads(capsys.readouterr().out) == {"name": "alpha", "value": 1}


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


def test_emit_writes_to_stdout_no_manual_echo(capsys: pytest.CaptureFixture[str]) -> None:
    """``emit`` writes the output itself — no forgotten-``echo`` footgun."""
    emit(_Widget(name="alpha", value=1), fmt="json")
    assert capsys.readouterr().out != ""


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
    assert f"— {expected}" in output
    assert "response:" not in output


def test_format_error_falls_back_to_raw_for_non_json_body() -> None:
    result = CliInvoker().invoke(_http_error_app("<html>nope</html>"), [])
    output = result.output or result.stderr
    assert "response: <html>nope</html>" in output


def test_format_error_falls_back_to_raw_for_unrecognised_json_shape() -> None:
    """Valid JSON with no known message key keeps the raw body (e.g. Jira's
    ``errorMessages``)."""
    result = CliInvoker().invoke(_http_error_app('{"errorMessages":["boom"]}'), [])
    output = result.output or result.stderr
    assert 'response: {"errorMessages":["boom"]}' in output


def test_format_error_keeps_raw_body_under_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("untaped.cli.is_verbose", lambda: True)
    result = CliInvoker().invoke(_http_error_app('{"message":"Bad credentials"}'), [])
    output = result.output or result.stderr
    assert "— Bad credentials" in output
    assert "response:" in output


def test_format_error_adds_transport_url_when_message_omits_it() -> None:
    app = create_app(name="test")

    @app.default
    def boom() -> None:
        with report_errors():
            raise HttpTransportError("connection failed", url="https://api.example.test/x")

    result = CliInvoker().invoke(app, [])
    output = result.output or result.stderr
    assert result.exit_code == 1
    assert "error: connection failed for https://api.example.test/x" in output


def test_format_error_does_not_duplicate_transport_url() -> None:
    app = create_app(name="test")

    @app.default
    def boom() -> None:
        with report_errors():
            raise HttpTransportError(
                "connection failed for https://api.example.test/x",
                url="https://api.example.test/x",
            )

    result = CliInvoker().invoke(app, [])
    output = result.output or result.stderr
    assert result.exit_code == 1
    assert output.count("https://api.example.test/x") == 1


# ---- --columns ? discoverability -------------------------------------------


def test_render_rows_columns_question_mark_lists_keys(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered = render_rows([{"name": "a", "value": 1}], fmt="table", columns=["?"])
    assert rendered == ""
    err = capsys.readouterr().err
    assert "name" in err
    assert "value" in err


def test_emit_columns_question_mark_lists_model_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit(_Widget(name="a", value=1), fmt="table", columns=["?"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "name" in captured.err
    assert "value" in captured.err
