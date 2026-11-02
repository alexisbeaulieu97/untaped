"""GitHub settings defaults."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from untaped.sdk import home
from untaped.sdk.health.deprecated_keys import key_mappings
from untaped.sdk.testing import invoke_root
from untaped_github.settings import SWEEP, GithubSettings


def test_inventory_defaults() -> None:
    inventory = GithubSettings().inventory
    assert (inventory.orgs, inventory.teams) == ([], [])


@pytest.mark.parametrize("sweep", [{"parallel": 0}, {"parallel": -2}])
def test_sweep_settings_reject_out_of_range_values(sweep: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        GithubSettings.model_validate({"sweep": sweep})


def _write(data: dict[str, object]) -> None:
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"format_version": 2, "github": data}))


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
