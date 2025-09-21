"""Contract tests for CLI list-directory command."""
import pytest
from click.testing import CliRunner

from untaped_cli.commands.github_list import list_directory


def test_list_directory_command_exists():
    """Test that the list-directory command is properly defined."""
    runner = CliRunner()
    result = runner.invoke(list_directory, ["--help"])
    assert result.exit_code == 0
    assert "list-directory" in result.output
    assert "List files in a GitHub repository directory" in result.output


def test_list_directory_command_requires_repository():
    """Test that the list-directory command requires a repository argument."""
    runner = CliRunner()
    result = runner.invoke(list_directory, [])
    assert result.exit_code != 0
    assert "Missing argument" in result.output.lower() or "required" in result.output.lower()


def test_list_directory_command_requires_directory_path():
    """Test that the list-directory command requires a directory path argument."""
    runner = CliRunner()
    result = runner.invoke(list_directory, ["owner/repo"])
    assert result.exit_code != 0
    assert "Missing argument" in result.output.lower() or "required" in result.output.lower()


def test_list_directory_command_accepts_config_file():
    """Test that the list-directory command accepts a config file option."""
    runner = CliRunner()
    result = runner.invoke(list_directory, ["--help"])
    assert result.exit_code == 0
    assert "--config-file" in result.output


def test_list_directory_command_accepts_variable_files():
    """Test that the list-directory command accepts variable files."""
    runner = CliRunner()
    result = runner.invoke(list_directory, ["--help"])
    assert result.exit_code == 0
    assert "--vars-file" in result.output


def test_list_directory_command_supports_dry_run():
    """Test that the list-directory command supports dry-run mode."""
    runner = CliRunner()
    result = runner.invoke(list_directory, ["--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output


def test_list_directory_command_accepts_recursive_option():
    """Test that the list-directory command accepts recursive listing option."""
    runner = CliRunner()
    result = runner.invoke(list_directory, ["--help"])
    assert result.exit_code == 0
    assert "--recursive" in result.output
