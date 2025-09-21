"""Contract tests for CLI read-file command."""
import pytest
from click.testing import CliRunner

from untaped_cli.commands.github_read import read_file


def test_read_file_command_exists():
    """Test that the read-file command is properly defined."""
    runner = CliRunner()
    result = runner.invoke(read_file, ["--help"])
    assert result.exit_code == 0
    assert "read-file" in result.output
    assert "Read a file from a GitHub repository" in result.output


def test_read_file_command_requires_repository():
    """Test that the read-file command requires a repository argument."""
    runner = CliRunner()
    result = runner.invoke(read_file, [])
    assert result.exit_code != 0
    assert "Missing argument" in result.output.lower() or "required" in result.output.lower()


def test_read_file_command_requires_file_path():
    """Test that the read-file command requires a file path argument."""
    runner = CliRunner()
    result = runner.invoke(read_file, ["owner/repo"])
    assert result.exit_code != 0
    assert "Missing argument" in result.output.lower() or "required" in result.output.lower()


def test_read_file_command_accepts_config_file():
    """Test that the read-file command accepts a config file option."""
    runner = CliRunner()
    result = runner.invoke(read_file, ["--help"])
    assert result.exit_code == 0
    assert "--config-file" in result.output


def test_read_file_command_accepts_variable_files():
    """Test that the read-file command accepts variable files."""
    runner = CliRunner()
    result = runner.invoke(read_file, ["--help"])
    assert result.exit_code == 0
    assert "--vars-file" in result.output


def test_read_file_command_supports_dry_run():
    """Test that the read-file command supports dry-run mode."""
    runner = CliRunner()
    result = runner.invoke(read_file, ["--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output
