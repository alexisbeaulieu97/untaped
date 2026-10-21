"""Shared fixtures for the jira tests: a registered ``jira`` section and a config."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.plugins.registry import section_settings
from untaped.settings import register_profile_settings
from untaped_jira import SPEC
from untaped_jira.settings import JiraSettings

BASE = "https://jira.example.com"


@pytest.fixture(autouse=True)
def _register_jira_settings(fresh_composition: None) -> None:
    # Invoking the jira app directly skips the SDK profile-settings registration;
    # the section is the composed model, with the token_command core adds.
    register_profile_settings("jira", section_settings(SPEC) or JiraSettings)


@pytest.fixture(autouse=True)
def jira_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(f"profiles:\n  default:\n    jira:\n      base_url: {BASE}\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    # Not in the file: a plaintext token there warns on every run.
    monkeypatch.setenv("UNTAPED_JIRA__TOKEN", "jira_pat")
    return cfg
