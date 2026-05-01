import typer
from typer.testing import CliRunner
from untaped_core import UntapedError, report_errors


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
