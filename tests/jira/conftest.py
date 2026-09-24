"""Shared fixtures for the jira tests: a registered ``jira`` section and a config."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.capabilities.jira.settings import JiraSettings
from untaped.settings import register_profile_settings

BASE = "https://jira.example.com"


@pytest.fixture(autouse=True)
def _register_jira_settings() -> Iterator[None]:
    # Invoking the jira app directly skips the SDK profile-settings registration.
    bootstrap._clear_for_tests()
    register_profile_settings("jira", JiraSettings)
    yield
    bootstrap._clear_for_tests()


@pytest.fixture(autouse=True)
def jira_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        f"profiles:\n  default:\n    jira:\n      base_url: {BASE}\n      token: jira_pat\n"
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg
