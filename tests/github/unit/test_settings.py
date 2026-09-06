from __future__ import annotations

from untaped.capabilities.github.settings import GithubSettings


def test_sweep_settings_defaults() -> None:
    settings = GithubSettings()

    assert settings.sweep.max_age_seconds == 3600
    assert settings.sweep.sync_concurrency == 12
