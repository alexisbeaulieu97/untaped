import json
from pathlib import Path

import pytest

from untaped import (
    HttpError,
    UntapedError,
    clamp_parallel,
    create_app,
    get_settings,
    parse_kv_pairs,
    render_rows,
    report_errors,
    resolve_each,
)
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


def test_report_errors_includes_http_response_body() -> None:
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
    assert "response:" in output
    assert "Resource not accessible by personal access token" in output


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


def test_resolve_each_includes_http_response_body(
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
    assert "response:" in err
    assert "missing permission" in err


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


def test_render_rows_structured_formats_ignore_theme(_isolated_config: Path) -> None:
    """json/raw output must stay byte-stable no matter the configured theme."""
    _isolated_config.write_text("ui:\n  theme: dark\n")
    get_settings.cache_clear()

    rows: list[dict[str, object]] = [{"name": "alpha", "value": "1"}]
    assert json.loads(render_rows(rows, fmt="json")) == [{"name": "alpha", "value": "1"}]
    raw = render_rows(rows, fmt="raw", columns=["name"])
    assert raw == "alpha"
    assert "\x1b[" not in raw
