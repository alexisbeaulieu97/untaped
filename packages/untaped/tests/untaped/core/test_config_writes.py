"""The config write use cases: what they write, and the lines they return instead of printing."""

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, Field

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
    page_size: int = Field(50, gt=0)


class SpelledSettings(BaseModel):
    """Settings with an old spelling still read and a retired one."""

    renamed_keys: ClassVar[dict[str, str]] = {
        "old_size": "size",
        "old_groups": "groups",
        "legacy_groups": "groups",
    }
    retired_keys: ClassVar[dict[str, str]] = {"a_size": "size", "a_groups": "groups"}

    size: int = 1
    groups: dict[str, dict[str, str]] = {}


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
    register_profile_settings("spelled", SpelledSettings)
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    return cfg


def test_set_writes_the_profile_and_returns_its_line(_config: Path) -> None:
    _config.write_text("format_version: 2\nprofiles:\n  work: {}\n", encoding="utf-8")
    change = SetSetting(SettingsFileRepository())("demo.page_size", "10", profile="work")
    assert read_config_dict(_config)["profiles"]["work"] == {"demo": {"page_size": 10}}
    assert (change.outcome.key, change.outcome.profile, change.outcome.action) == (
        "demo.page_size",
        "work",
        "updated",
    )
    assert [kind for kind, _ in change.notes] == ["success"]
    assert change.notes[0][1].startswith("set demo.page_size in profile work (config: ")


def test_a_refused_value_names_its_key_once(_config: Path) -> None:
    with pytest.raises(ConfigError) as refused:
        SetSetting(SettingsFileRepository())("demo.page_size", "-5")
    assert str(refused.value) == (
        "invalid value for 'demo.page_size': Input should be greater than 0"
    )


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
        "format_version: 2\ndemo: {page_size: 1}\nprofiles:\n  work:\n    demo: {page_size: 2}\n",
        encoding="utf-8",
    )
    change = UnsetSetting(SettingsFileRepository())("demo.page_size", profile="work")
    assert change.outcome.profile == "work"
    written = read_config_dict(_config)
    assert written["demo"] == {"page_size": 1}
    assert not written["profiles"]["work"].get("demo")


def test_set_refuses_a_value_changed_since_it_was_read(_config: Path) -> None:
    _config.write_text("format_version: 2\ndemo: {page_size: 5}\n", encoding="utf-8")
    use_case = SetSetting(SettingsFileRepository())
    expected = use_case.current("demo.page_size", profile="default")
    assert expected == 5
    use_case("demo.page_size", "6", profile="default")  # another writer

    with pytest.raises(ConfigError, match="changed in profile default while it was open") as exc:
        use_case("demo.page_size", "7", profile="default", expected=expected)

    assert exc.value.category == "conflict"
    assert read_config_dict(_config)["demo"] == {"page_size": 6}
    use_case("demo.page_size", "8", profile="default", expected=6)  # read again: it writes
    assert read_config_dict(_config)["demo"] == {"page_size": 8}


def test_current_is_none_for_a_profile_that_does_not_exist(_config: Path) -> None:
    _config.write_text("format_version: 2\n", encoding="utf-8")
    assert SetSetting(SettingsFileRepository()).current("demo.page_size", profile="new") is None


def test_set_settings_writes_keys_only_valid_together_in_one_change(_config: Path) -> None:
    repo = SettingsFileRepository()
    with pytest.raises(ConfigError, match=r"invalid value for 'pair\.server\.url'"):
        SetSetting(repo)("pair.server.url", "https://a")  # alone, the server lacks its org

    changes = SetSettings(repo)(
        {"pair.server.url": "https://a", "pair.server.org": "acme", "pair.retries": None},
        profile="default",
    )

    assert read_config_dict(_config) == {
        "format_version": 2,
        "pair": {"server": {"url": "https://a", "org": "acme"}},
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
        "format_version: 2\npair:\n  server: {url: 'https://a', org: other}\n",
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
    _config.write_text("format_version: 2\npair: {retries: 5}\n", encoding="utf-8")
    (change,) = SetSettings(SettingsFileRepository())({"pair.retries": None})
    assert change.outcome.action == "deleted"
    assert change.notes == (("success", "unset pair.retries in profile default"),)
    assert not read_config_dict(_config).get("pair")


def test_a_retired_spelling_is_no_ones_value(_config: Path) -> None:
    """A write's ``expected`` and a prompt's default see what a run reads, never a retired key."""
    _config.write_text("format_version: 2\nspelled: {a_size: 9}\n", encoding="utf-8")
    repo = SettingsFileRepository()
    assert repo.own_value("spelled.size") is None

    _config.write_text("format_version: 2\nspelled: {a_size: 9, old_size: 5}\n", encoding="utf-8")
    assert SettingsFileRepository().own_value("spelled.size") == 5


def test_an_entry_is_read_from_the_map_a_run_reads(_config: Path) -> None:
    """Under the map's renamed spelling when that is all there is; never past the map's name."""
    _config.write_text(
        "format_version: 2\nspelled:\n  a_groups: {ops: {lead: ana}}\n"
        "  old_groups: {dev: {lead: bo}}\n",
        encoding="utf-8",
    )
    repo = SettingsFileRepository()
    assert repo.own_value("spelled.groups.dev.lead") == "bo"
    assert repo.own_value("spelled.groups.ops") is None

    _config.write_text(
        "format_version: 2\nspelled:\n  groups: {qa: {lead: cy}}\n"
        "  old_groups: {dev: {lead: bo}}\n",
        encoding="utf-8",
    )
    assert SettingsFileRepository().own_value("spelled.groups.dev") is None


@pytest.mark.parametrize(
    "spelled",
    [
        "groups: null\n  old_groups: {dev: {lead: bo}}",
        "legacy_groups: null\n  old_groups: {dev: {lead: bo}}",
    ],
    ids=["its-name", "its-closest-old-spelling"],
)
def test_a_null_spelling_a_run_reads_hides_the_others(_config: Path, spelled: str) -> None:
    """A run reads the null (its name, or the closest old spelling) and ignores ``old_groups``."""
    _config.write_text(f"format_version: 2\nspelled:\n  {spelled}\n", encoding="utf-8")
    assert SettingsFileRepository().own_value("spelled.groups.dev.lead") is None
    SetSettings(SettingsFileRepository())({"spelled.groups.qa.lead": "cy"})
    assert read_config_dict(_config)["spelled"] == {"groups": {"qa": {"lead": "cy"}}}


def test_removing_an_entry_that_isnt_set_leaves_the_map_runs_read(_config: Path) -> None:
    """Only the map's retired spelling goes, so ``config migrate`` can't bring it back."""
    _config.write_text(
        "format_version: 2\nspelled:\n  groups: {qa: {lead: cy}}\n  a_groups: {ops: {lead: ana}}\n",
        encoding="utf-8",
    )
    (change,) = SetSettings(SettingsFileRepository())({"spelled.groups.dev": None})
    assert change.outcome.action == "deleted"
    assert read_config_dict(_config)["spelled"] == {"groups": {"qa": {"lead": "cy"}}}


def test_removing_an_entry_with_no_spelling_to_drop_changes_nothing(_config: Path) -> None:
    text = "format_version: 2\nspelled:\n  groups: {qa: {lead: cy}}\n"
    _config.write_text(text, encoding="utf-8")
    (change,) = SetSettings(SettingsFileRepository())({"spelled.groups.dev": None})
    assert change.outcome.action == "unchanged"
    assert _config.read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    ("key", "old"), [("spelled.groups", "a_groups"), ("spelled.size", "a_size")]
)
def test_removing_a_key_that_isnt_set_drops_its_retired_spelling(
    _config: Path, key: str, old: str
) -> None:
    """A retired spelling no run reads still goes, and is named, so migrate can't restore it."""
    text = "format_version: 2\nspelled:\n  a_groups: {ops: {lead: ana}}\n  a_size: 9\n"
    _config.write_text(text, encoding="utf-8")
    (change,) = SetSettings(SettingsFileRepository())({key: None})
    assert change.outcome.action == "deleted"
    assert change.notes == (("success", f"unset {key} in profile default; removed spelled.{old}"),)
    assert old not in read_config_dict(_config)["spelled"]

    _config.write_text(text, encoding="utf-8")
    unset = UnsetSetting(SettingsFileRepository())(key)
    assert unset.outcome.action == "deleted"
    assert unset.notes == (("success", f"unset {key} in profile default; removed spelled.{old}"),)
    again = UnsetSetting(SettingsFileRepository())(key)
    assert again.outcome.action == "unchanged"
    assert again.notes == (("info", f"{key} was not set in profile default"),)


def test_removing_a_null_a_run_reads_is_a_change(_config: Path) -> None:
    """A null is set, as a run reads it: removing it writes the file and says so."""
    _config.write_text("format_version: 2\nspelled:\n  size: null\n  a_size: 9\n", encoding="utf-8")
    (change,) = SetSettings(SettingsFileRepository())({"spelled.size": None})
    assert change.outcome.action == "deleted"
    assert change.notes == (
        ("success", "unset spelled.size in profile default; removed spelled.a_size"),
    )
    assert not read_config_dict(_config).get("spelled")


def test_removing_a_key_under_a_null_section_changes_nothing(_config: Path) -> None:
    """Nothing is set under a null section, so nothing is validated or blamed either."""
    _config.write_text("format_version: 2\nspelled: null\n", encoding="utf-8")
    (change,) = SetSettings(SettingsFileRepository())({"spelled.size": None})
    assert change.outcome.action == "unchanged"


def test_setting_a_value_the_key_holds_is_unchanged(_config: Path) -> None:
    """The outcome follows the file: a value already there writes nothing, dry run or not."""
    text = "format_version: 2\ndemo: {page_size: 10}\n"
    _config.write_text(text, encoding="utf-8")
    repo = SettingsFileRepository()
    for dry_run in (True, False):
        change = SetSetting(repo)("demo.page_size", "10", dry_run=dry_run)
        assert change.outcome.action == "unchanged"
        assert change.notes == (
            ("info", "demo.page_size already holds that value in profile default"),
        )
    (many,) = SetSettings(repo)({"demo.page_size": "10"})
    assert (many.outcome.action, many.notes) == ("unchanged", ())
    assert _config.read_text(encoding="utf-8") == text


def test_setting_a_value_still_drops_its_old_spelling(_config: Path) -> None:
    """The value is the same, but removing the old spelling changes the file."""
    _config.write_text("format_version: 2\nspelled: {size: 4, a_size: 9}\n", encoding="utf-8")
    change = SetSetting(SettingsFileRepository())("spelled.size", "4")
    assert change.outcome.action == "updated"
    assert read_config_dict(_config)["spelled"] == {"size": 4}


@pytest.mark.parametrize(
    "write",
    [
        lambda: UnsetSetting(SettingsFileRepository())("demo.page_size", profile="prod"),
        lambda: SetSettings(SettingsFileRepository())({"demo.page_size": None}, profile="prod")[0],
    ],
    ids=["unset", "set-settings"],
)
def test_a_write_that_changes_nothing_leaves_an_empty_profile_alone(
    _config: Path, write: Callable[[], object]
) -> None:
    """An empty ``prod:`` stays as written, not rewritten as ``prod: {}``."""
    _config.write_text("format_version: 2\nprofiles:\n  prod:\n", encoding="utf-8")
    before = _config.read_text(encoding="utf-8")
    change = write()
    assert change.outcome.action == "unchanged"  # type: ignore[attr-defined]
    assert _config.read_text(encoding="utf-8") == before
