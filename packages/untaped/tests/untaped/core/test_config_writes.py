"""The config write use cases: what they write, and the lines they return instead of printing."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.core.config import (
    RankProviders,
    SetSetting,
    SetSettings,
    SettingsFileRepository,
    UnsetSetting,
)
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.settings import register_profile_settings
from untaped.sdk.errors import ConfigError, UsageError


class DemoSettings(BaseModel):
    page_size: int = 50


class PairServer(BaseModel):
    url: str
    org: str


class PairSettings(BaseModel):
    """A block whose two fields are only valid together."""

    server: PairServer | None = None
    retries: int = 3


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    register_profile_settings("demo", DemoSettings)
    register_profile_settings("pair", PairSettings)
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
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


def test_set_refuses_a_value_changed_since_it_was_read(_config: Path) -> None:
    _config.write_text("profiles:\n  default:\n    demo: {page_size: 5}\n", encoding="utf-8")
    use_case = SetSetting(SettingsFileRepository())
    expected = use_case.current("demo.page_size", profile="default")
    assert expected == 5
    use_case("demo.page_size", "6", profile="default")  # another writer

    with pytest.raises(ConfigError, match="changed in profile default while it was open") as exc:
        use_case("demo.page_size", "7", profile="default", expected=expected)

    assert exc.value.category == "conflict"
    assert read_config_dict(_config)["profiles"]["default"]["demo"] == {"page_size": 6}
    use_case("demo.page_size", "8", profile="default", expected=6)  # read again: it writes
    assert read_config_dict(_config)["profiles"]["default"]["demo"] == {"page_size": 8}


def test_current_is_none_for_a_profile_that_does_not_exist(_config: Path) -> None:
    _config.write_text("profiles:\n  default: {}\n", encoding="utf-8")
    assert SetSetting(SettingsFileRepository()).current("demo.page_size", profile="new") is None


def test_set_settings_writes_keys_only_valid_together_in_one_change(_config: Path) -> None:
    repo = SettingsFileRepository()
    with pytest.raises(ConfigError, match=r"invalid value for 'pair\.server\.url'"):
        SetSetting(repo)("pair.server.url", "https://a")  # alone, the server lacks its org

    changes = SetSettings(repo)(
        {"pair.server.url": "https://a", "pair.server.org": "acme", "pair.retries": None},
        profile="default",
    )

    assert read_config_dict(_config)["profiles"]["default"] == {
        "pair": {"server": {"url": "https://a", "org": "acme"}}
    }
    assert [(c.outcome.key, c.outcome.action) for c in changes] == [
        ("pair.server.url", "updated"),
        ("pair.server.org", "updated"),
        ("pair.retries", "unchanged"),
    ]
    assert changes[0].notes == (("success", "set pair.server.url in profile default"),)


def test_set_settings_writes_nothing_when_one_value_is_refused(_config: Path) -> None:
    with pytest.raises(ConfigError, match=r"invalid value for 'pair\.retries'"):
        SetSettings(SettingsFileRepository())(
            {"pair.server.url": "https://a", "pair.server.org": "acme", "pair.retries": "many"}
        )
    assert not _config.exists()


def test_set_settings_refuses_every_key_when_one_changed_since_it_was_read(
    _config: Path,
) -> None:
    _config.write_text(
        "profiles:\n  default:\n    pair:\n      server: {url: 'https://a', org: other}\n",
        encoding="utf-8",
    )
    before = _config.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match=r"pair\.server\.org changed in profile default"):
        SetSettings(SettingsFileRepository())(
            {"pair.server.url": "https://b", "pair.server.org": "acme"},
            expected={"pair.server.url": "https://a", "pair.server.org": "acme"},
        )
    assert _config.read_text(encoding="utf-8") == before


def test_set_settings_unsets_a_key_the_profile_set(_config: Path) -> None:
    _config.write_text("profiles:\n  default:\n    pair: {retries: 5}\n", encoding="utf-8")
    (change,) = SetSettings(SettingsFileRepository())({"pair.retries": None})
    assert change.outcome.action == "deleted"
    assert change.notes == (("success", "unset pair.retries in profile default"),)
    assert not read_config_dict(_config)["profiles"]["default"].get("pair")
