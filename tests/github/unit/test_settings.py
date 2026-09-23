from __future__ import annotations

import pytest
from pydantic import ValidationError

from untaped.capabilities.github.settings import GithubSettings


def test_sweep_settings_defaults() -> None:
    settings = GithubSettings()

    assert settings.sweep.max_age_seconds == 3600
    assert settings.sweep.sync_concurrency == 12


def test_settings_are_frozen() -> None:
    settings = GithubSettings()

    with pytest.raises(ValidationError):
        settings.token = None  # type: ignore[misc]
    with pytest.raises(ValidationError):
        settings.sweep.max_age_seconds = 1  # type: ignore[misc]
