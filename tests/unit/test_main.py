from typer.testing import CliRunner

from untaped.main import app


def test_help_lists_all_domains() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "config" in output
    assert "workspace" in output
    assert "awx" in output
    assert "github" in output


def test_workspace_subcommand_help() -> None:
    result = CliRunner().invoke(app, ["workspace", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout


def test_config_subcommand_help() -> None:
    result = CliRunner().invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "set" in result.stdout
    assert "unset" in result.stdout
