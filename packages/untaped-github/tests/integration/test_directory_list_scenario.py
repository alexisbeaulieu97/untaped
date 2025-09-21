"""Integration test for directory listing scenario."""
import pytest
import tempfile
import os
from pathlib import Path

from untaped_cli.commands.github_list import list_directory


@pytest.mark.integration
def test_list_directory_contents():
    """Test listing files in a GitHub repository directory."""
    # This test will fail until the CLI command is implemented
    with pytest.raises(Exception):
        # Create a temporary YAML config file
        config_content = """
        repository: "octocat/Hello-World"
        directory_path: "."
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_file = f.name

        try:
            # This should work when implemented
            result = list_directory.callback(config_file=config_file)
            assert isinstance(result, list)
            assert len(result) > 0
            # Should contain at least README.md
            filenames = [item.get('name', '') for item in result]
            assert any('README' in filename for filename in filenames)
        finally:
            os.unlink(config_file)


@pytest.mark.integration
def test_list_directory_recursive():
    """Test listing directory contents recursively."""
    # This test will fail until the CLI command is implemented
    with pytest.raises(Exception):
        config_content = """
        repository: "octocat/Hello-World"
        directory_path: "."
        recursive: true
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_file = f.name

        try:
            # This should work when implemented
            result = list_directory.callback(config_file=config_file)
            assert isinstance(result, list)
            assert len(result) > 0
        finally:
            os.unlink(config_file)


@pytest.mark.integration
def test_list_directory_with_templated_path():
    """Test listing directory with templated path variables."""
    # This test will fail until the CLI command is implemented
    with pytest.raises(Exception):
        # Create config with template variables
        config_content = """
        repository: "{{ owner }}/{{ repo }}"
        directory_path: "{{ path }}"
        """

        # Create variables file
        vars_content = """
        owner: "octocat"
        repo: "Hello-World"
        path: "."
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_file = f.name

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(vars_content)
            vars_file = f.name

        try:
            # This should work when implemented
            result = list_directory.callback(config_file=config_file, vars_file=vars_file)
            assert isinstance(result, list)
            assert len(result) > 0
        finally:
            os.unlink(config_file)
            os.unlink(vars_file)
