import pytest
import typer
from typer.testing import CliRunner
from untaped_core import UntapedError, parse_kv_pairs, report_errors, resolve_each


def test_clean_message_for_untaped_error() -> None:
    app = typer.Typer()

    @app.command()
    def boom() -> None:
        with report_errors():
            raise UntapedError("something went wrong")

    result = CliRunner().invoke(app, [])
    assert result.exit_code == 1
    assert "error: something went wrong" in (result.output or result.stderr)


def test_passes_through_non_untaped_exception() -> None:
    """Non-UntapedError exceptions should not be swallowed — they're bugs."""
    app = typer.Typer()

    @app.command()
    def boom() -> None:
        with report_errors():
            raise ValueError("bug")

    result = CliRunner().invoke(app, [])
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
    with pytest.raises(typer.BadParameter, match="--filter expects KEY=VALUE"):
        parse_kv_pairs(["bogus"], flag="--filter")


def test_parse_kv_pairs_rejects_empty_key() -> None:
    with pytest.raises(typer.BadParameter, match="--filter expects KEY=VALUE"):
        parse_kv_pairs(["=value"], flag="--filter")


def test_parse_kv_pairs_rejects_whitespace_only_key() -> None:
    with pytest.raises(typer.BadParameter, match="--var expects KEY=VALUE"):
        parse_kv_pairs(["   =value"], flag="--var")


def test_parse_kv_pairs_error_uses_provided_flag_name() -> None:
    with pytest.raises(typer.BadParameter, match="--custom expects KEY=VALUE"):
        parse_kv_pairs(["bogus"], flag="--custom")


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


def test_resolve_each_propagates_non_untaped_exceptions() -> None:
    """Non-UntapedError exceptions are bugs and must surface, not be swallowed."""

    def fn(n: str) -> str:
        raise ValueError("bug")

    with pytest.raises(ValueError, match="bug"):
        resolve_each(["x"], fn)
