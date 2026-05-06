import pytest
import typer
from typer.testing import CliRunner
from untaped_core import UntapedError, parse_kv_pairs, report_errors


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
