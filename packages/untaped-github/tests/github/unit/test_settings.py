"""GitHub settings defaults."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from untaped.deprecated_keys import key_mappings
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


def _write(data: dict[str, object]) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"profiles": {"default": {"github": data}}}))


def test_a_retired_key_is_not_read(capsys: pytest.CaptureFixture[str]) -> None:
    _write({"sweep": {"sync_concurrency": 3}})

    settings = get_config_section("github", GithubSettings)

    assert settings.sweep.parallel == GithubSettings().sweep.parallel != 3
    assert "sweep.sync_concurrency" not in capsys.readouterr().err


def test_the_corpus_directory_settings_are_deleted() -> None:
    """``cache_dir`` (and ``corpus_path`` before it) gave way to ``git.store_dir``."""
    deleted = key_mappings(GithubSettings).deleted

    assert {key: entry.reason() for key, entry in deleted.items()} == {
        "cache_dir": "deleted in 11.0; the repo store lives under git.store_dir",
        "corpus_path": "deleted in 11.0 (via cache_dir); the repo store lives under git.store_dir",
    }
    assert "cache_dir" not in GithubSettings.model_fields


@pytest.mark.parametrize("data", [{"cache_dir": "/c"}, {"corpus_path": "/c"}])
def test_a_deleted_corpus_directory_is_not_read(data: dict[str, object]) -> None:
    _write(data)

    settings = get_config_section("github", GithubSettings)

    assert not hasattr(settings, "cache_dir")


@pytest.mark.parametrize("protocol", ["https", "ssh"])
def test_git_protocol_takes_https_or_ssh(protocol: str) -> None:
    assert GithubSettings().git_protocol == "https"
    assert GithubSettings.model_validate({"git_protocol": protocol}).git_protocol == protocol
    with pytest.raises(ValidationError):
        GithubSettings.model_validate({"git_protocol": "git"})
