"""GitHub settings defaults."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from untaped.sdk import get_config_section, home
from untaped.sdk.health.deprecated_keys import key_mappings
from untaped.sdk.testing import invoke_root
from untaped_github.settings import SWEEP, GithubSettings


def test_inventory_defaults() -> None:
    inventory = GithubSettings().inventory
    assert (inventory.orgs, inventory.teams) == ([], [])


def test_the_inventory_file_settings_are_deleted() -> None:
    """github keeps no inventory file: workspace's answer cache holds the repos it lists."""
    deleted = key_mappings(GithubSettings).deleted
    path, max_age = deleted["inventory.path"], deleted["inventory.max_age_seconds"]

    assert path.reason() == max_age.reason()
    assert path.reason().startswith("deleted in 11.0; workspace keeps the repos github lists")
    assert set(GithubSettings().inventory.model_dump()) == {"orgs", "teams"}


@pytest.mark.parametrize(
    "inventory", [{"path": "/tmp/inv.json"}, {"max_age_seconds": 0}], ids=["path", "max-age"]
)
def test_a_deleted_inventory_setting_is_not_read(inventory: dict[str, object]) -> None:
    _write({"inventory": {**inventory, "orgs": ["acme"]}})

    settings = get_config_section("github", GithubSettings)

    assert settings.inventory.orgs == ["acme"]
    assert set(settings.inventory.model_dump()) == {"orgs", "teams"}


@pytest.mark.parametrize("sweep", [{"parallel": 0}, {"parallel": -2}])
def test_sweep_settings_reject_out_of_range_values(sweep: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        GithubSettings.model_validate({"sweep": sweep})


def _write(data: dict[str, object]) -> None:
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"format_version": 2, "github": data}))


def test_a_retired_key_is_not_read(capsys: pytest.CaptureFixture[str]) -> None:
    _write({"sweep": {"sync_concurrency": 3}})

    settings = get_config_section("github", GithubSettings)

    assert settings.sweep.parallel == GithubSettings().sweep.parallel != 3
    assert "sweep.sync_concurrency" not in capsys.readouterr().err


def test_the_corpus_directory_settings_are_deleted() -> None:
    """``cache_dir`` (and ``corpus_path`` before it) gave way to ``git.store_dir``."""
    deleted = key_mappings(GithubSettings).deleted

    corpus = {key: deleted[key].reason() for key in ("cache_dir", "corpus_path")}
    assert corpus == {
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


def test_the_11x_token_settings_and_sweep_max_age_are_deleted_in_12() -> None:
    """The token is stored with ``untaped auth login github``; the max age is the cache's."""
    deleted = key_mappings(GithubSettings).deleted

    assert deleted["token"].reason() == deleted["token_command"].reason()
    assert deleted["token"].reason() == (
        "deleted in 12.0; store the token with `untaped auth login github`"
    )
    assert deleted["sweep.max_age_seconds"].reason() == (
        "deleted in 12.0; use github.caches.sweep.max_age_seconds"
    )


@pytest.mark.usefixtures("composed")
def test_the_sweep_max_age_is_the_sweep_caches_setting() -> None:
    assert SWEEP.setting == "github.caches.sweep.max_age_seconds"
    assert "max_age_seconds" not in GithubSettings().sweep.model_dump()


def test_an_11x_sweep_max_age_is_not_read() -> None:
    _write({"sweep": {"max_age_seconds": 60}})

    result = invoke_root(["config", "get", "github.caches.sweep.max_age_seconds"])

    assert result.stdout.strip() == "3600"
