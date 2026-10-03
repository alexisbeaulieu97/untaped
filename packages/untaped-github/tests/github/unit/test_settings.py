"""GitHub settings defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from untaped_github.settings import GithubSettings


def test_inventory_defaults() -> None:
    inventory = GithubSettings().inventory
    assert inventory.path == Path("~/.untaped/github-inventory.json")
    assert (inventory.orgs, inventory.teams, inventory.max_age_seconds) == ([], [], 86400)


def test_inventory_max_age_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        GithubSettings.model_validate({"inventory": {"max_age_seconds": 0}})


@pytest.mark.parametrize(
    "sweep", [{"max_age_seconds": -1}, {"sync_concurrency": 0}, {"sync_concurrency": -2}]
)
def test_sweep_settings_reject_out_of_range_values(sweep: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        GithubSettings.model_validate({"sweep": sweep})
