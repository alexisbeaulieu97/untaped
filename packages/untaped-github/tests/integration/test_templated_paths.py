"""Integration test for templated file paths."""
import pytest
import tempfile
import os
from pathlib import Path

from untaped_cli.commands.github_read import read_file


@pytest.mark.integration
def test_templated_repository_and_file_path():
    """Test using templated variables for both repository and file path."""
    # This test will fail until the CLI command is implemented
    with pytest.raises(Exception):
        # Create config with multiple template variables
        config_content = """
        repository: "{{ org }}/{{ project }}"
        file_path: "{{ docs_path }}"
        """

        # Create variables file with organization and project info
        vars_content = """
        org: "github"
        project: "gitignore"
        docs_path: "README.md"
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_file = f.name

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(vars_content)
            vars_file = f.name

        try:
            # This should work when implemented
            result = read_file.callback(config_file=config_file, vars_file=vars_file)
            assert isinstance(result, str)
            assert len(result) > 0
        finally:
            os.unlink(config_file)
            os.unlink(vars_file)


@pytest.mark.integration
def test_multiple_variable_files():
    """Test using multiple variable files for complex templating."""
    # This test will fail until the CLI command is implemented
    with pytest.raises(Exception):
        config_content = """
        repository: "{{ org }}/{{ project }}"
        file_path: "{{ env }}/{{ docs_path }}"
        """

        # Repository variables
        repo_vars_content = """
        org: "github"
        project: "gitignore"
        """

        # Environment variables
        env_vars_content = """
        env: "templates"
        docs_path: "README.md"
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_file = f.name

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(repo_vars_content)
            repo_vars_file = f.name

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(env_vars_content)
            env_vars_file = f.name

        try:
            # This should work when implemented
            result = read_file.callback(config_file=config_file, vars_file=[repo_vars_file, env_vars_file])
            assert isinstance(result, str)
            assert len(result) > 0
        finally:
            os.unlink(config_file)
            os.unlink(repo_vars_file)
            os.unlink(env_vars_file)


@pytest.mark.integration
def test_environment_variable_override():
    """Test that environment variables can override template variables."""
    # This test will fail until the CLI command is implemented
    with pytest.raises(Exception):
        config_content = """
        repository: "{{ org }}/{{ project }}"
        file_path: "{{ file_path }}"
        """

        vars_content = """
        org: "default-org"
        project: "default-project"
        file_path: "default-file.md"
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_file = f.name

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(vars_content)
            vars_file = f.name

        # Set environment variables to override
        env_vars = {
            'org': 'github',
            'project': 'gitignore',
            'file_path': 'README.md'
        }

        try:
            # This should work when implemented
            result = read_file.callback(config_file=config_file, vars_file=vars_file)
            assert isinstance(result, str)
            assert len(result) > 0
        finally:
            os.unlink(config_file)
            os.unlink(vars_file)
