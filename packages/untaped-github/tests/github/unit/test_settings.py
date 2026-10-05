"""GitHub settings defaults."""

from __future__ import annotations

import os
from operator import attrgetter
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from untaped.sdk import get_config_section
from untaped_github.settings import GithubSettings


def test_inventory_defaults() -> None:
    inventory = GithubSettings().inventory
    assert inventory.path == Path("~/.untaped/github-inventory.json")
    assert (inventory.orgs, inventory.teams, inventory.max_age_seconds) == ([], [], 86400)


def test_inventory_max_age_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        GithubSettings.model_validate({"inventory": {"max_age_seconds": 0}})


@pytest.mark.parametrize("sweep", [{"max_age_seconds": -1}, {"parallel": 0}, {"parallel": -2}])
def test_sweep_settings_reject_out_of_range_values(sweep: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        GithubSettings.model_validate({"sweep": sweep})


@pytest.mark.parametrize(
    ("data", "old", "new", "value"),
    [
        ({"corpus_path": "/c"}, "corpus_path", "cache_dir", Path("/c")),
        ({"sweep": {"sync_concurrency": 3}}, "sweep.sync_concurrency", "sweep.parallel", 3),
    ],
)
def test_an_old_key_is_read_as_the_new_one(
    capsys: pytest.CaptureFixture[str], data: dict[str, object], old: str, new: str, value: object
) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"profiles": {"default": {"github": data}}}))

    assert attrgetter(new)(get_config_section("github", GithubSettings)) == value
    assert (
        f"warning: github.{old} is deprecated and will be removed in the next major release; "
        f"use github.{new}"
    ) in capsys.readouterr().err
