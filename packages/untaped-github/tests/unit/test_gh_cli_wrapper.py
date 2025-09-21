"""Unit tests for GitHub CLI wrapper."""
import json
import pytest
from unittest.mock import Mock, patch, MagicMock

from untaped_github.gh_cli_wrapper import GitHubCliWrapper, GitHubCliError