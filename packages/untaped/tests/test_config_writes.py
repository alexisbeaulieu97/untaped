"""The config write use cases: what they write, and the lines they return instead of printing."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.config import RankProviders, SetSetting, SettingsFileRepository, UnsetSetting
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError, UsageError
from untaped.settings import register_profile_settings


class DemoSettings(BaseModel):
    page_size: int = 50


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    register_profile_settings("demo", DemoSettings)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


def test_set_writes_the_profile_and_returns_its_line(_config: Path) -> None:
    _config.write_text("profiles:\n  default: {}\n  work: {}\n", encoding="utf-8")
    change = SetSetting(SettingsFileRepository())("demo.page_size", "10", profile="work")
    assert read_config_dict(_config)["profiles"]["work"] == {"demo": {"page_size": 10}}
    assert (change.outcome.key, change.outcome.profile, change.outcome.action) == (
        "demo.page_size",
        "work",
        "updated",
    )
    assert [kind for kind, _ in change.notes] == ["success"]
    assert change.notes[0][1].startswith("set demo.page_size in profile work (config: ")


def test_set_refuses_an_invalid_value_and_writes_nothing(_config: Path) -> None:
    with pytest.raises(ConfigError, match=r"invalid value for 'demo\.page_size'"):
        SetSetting(SettingsFileRepository())("demo.page_size", "many")
    assert not _config.exists()


def test_a_dry_run_set_plans_without_a_line(_config: Path) -> None:
    change = SetSetting(SettingsFileRepository())("demo.page_size", "10", dry_run=True)
    assert change.outcome.action == "planned"
    assert change.notes == ()
    assert not _config.exists()


def test_unset_removes_the_key_and_says_so_or_that_it_was_not_set(_config: Path) -> None:
    repo = SettingsFileRepository()
    SetSetting(repo)("demo.page_size", "10")
    removed = UnsetSetting(repo)("demo.page_size")
    assert removed.outcome.action == "deleted"
    assert removed.notes == (("success", "unset demo.page_size in profile default"),)
    again = UnsetSetting(repo)("demo.page_size")
    assert again.outcome.action == "unchanged"
    assert again.notes == (("info", "demo.page_size was not set in profile default"),)


def test_rank_refuses_an_owner_that_owns_no_contract() -> None:
    rank = RankProviders(SettingsFileRepository(), owners=["workspace"])
    with pytest.raises(UsageError, match=r"'demo' owns no contract \(contract owners: workspace\)"):
        rank("demo.source", "repos", ["github"])
    with pytest.raises(UsageError, match=r"name the contract as OWNER\.CONTRACT"):
        rank("demo", "repos", [])


def test_unset_works_on_the_named_profile(_config: Path) -> None:
    _config.write_text(
        "profiles:\n  default:\n    demo: {page_size: 1}\n  work:\n    demo: {page_size: 2}\n",
        encoding="utf-8",
    )
    change = UnsetSetting(SettingsFileRepository())("demo.page_size", profile="work")
    assert change.outcome.profile == "work"
    profiles = read_config_dict(_config)["profiles"]
    assert profiles["default"] == {"demo": {"page_size": 1}}
    assert not profiles["work"].get("demo")
