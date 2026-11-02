"""A setting key steps into a ``dict[str, X]`` setting by entry name (``shelf.sources.main.url``).

``config get/set/unset`` reach one entry, and a setting inside it; the entry
inherits the map's marks; ``key()`` names an entry with ``[name]``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, ClassVar, Literal

import pytest
import yaml
from pydantic import BaseModel, SecretStr, model_validator

from core_test_support.commands.support import (
    config_root,
    make_plugin,
    plugin_candidate,
    write_config,
)
from untaped.runtime import bootstrap
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.folder_layer import forget_folder_layer
from untaped.runtime.profile_resolver import DEFAULT_PROFILE
from untaped.runtime.settings_env import env_var_name
from untaped.sdk import key
from untaped.sdk._keys import SettingKey, model_in, value_in
from untaped.sdk._table import _column_name
from untaped.sdk.health import HomeOnly
from untaped.sdk.health.deprecated_keys import Renamed
from untaped.sdk.marks import experimental
from untaped.sdk.storage import Dir
from untaped.sdk.testing import CliInvoker, CliResult, invoke_cli, make_project

pytestmark = pytest.mark.usefixtures("_isolated_config")


class Item(BaseModel):
    enabled: bool = True
    policy: Literal["sync", "once", "manual"] | None = None


class Source(BaseModel):
    url: str | None = None
    packages: dict[str, Item] = {}

    @model_validator(mode="after")
    def _needs_url(self) -> Source:
        if self.url is None:
            raise ValueError("needs url")
        return self


class Server(BaseModel):
    url: str
    token: SecretStr | None = None


class ShelfSettings(BaseModel):
    renamed_keys: ClassVar[Mapping[str, object]] = {"old_groups": Renamed("groups", removed_in=99)}

    sources: Annotated[dict[str, Source], HomeOnly()] = {}
    labels: dict[str, str] = {}
    tokens: dict[str, SecretStr] = {}
    trial: Annotated[dict[str, Item], experimental] = {}
    groups: dict[str, dict[str, str]] = {}
    servers: dict[str, Server] = {}


HOME = """\
format_version: 2
shelf:
  sources:
    main:
      url: https://git.example.com/main.git
      packages:
        git: {policy: once}
  labels: {Upper: kept}
profiles:
  work: {}
"""


@pytest.fixture(autouse=True)
def _home(_isolated_config: Path) -> None:
    write_config(_isolated_config, HOME)


def _config(*args: str) -> CliResult:
    return CliInvoker().invoke(
        config_root(make_plugin("shelf", settings=ShelfSettings)), list(args)
    )


def _shelf(path: Path) -> dict[str, object]:
    shelf = read_config_dict(path)["shelf"]
    assert isinstance(shelf, dict)
    return shelf


def _get(key_: str) -> dict[str, object]:
    result = _config("get", key_, "-f", "json")
    assert result.exit_code == 0, result.output
    entry = json.loads(result.stdout)
    assert isinstance(entry, dict)
    return entry


# ── get, set and unset ───────────────────────────────────────────────────────


def test_set_and_get_a_setting_inside_an_entry(_isolated_config: Path) -> None:
    result = _config("set", "shelf.sources.main.packages.git.policy", "manual")
    assert result.exit_code == 0, result.output
    assert "set shelf.sources.main.packages.git.policy in profile default" in result.stderr
    assert _shelf(_isolated_config)["sources"] == {
        "main": {
            "url": "https://git.example.com/main.git",
            "packages": {"git": {"policy": "manual"}},
        }
    }
    got = _get("shelf.sources.main.packages.git.policy")
    assert (got["value"], got["source"]) == ("manual", "base")
    assert _get("shelf.sources.main.packages.git.enabled")["value"] is True


def test_an_entry_is_set_as_json_or_yaml_and_unset_whole(_isolated_config: Path) -> None:
    added = _config("set", "shelf.sources.main.packages.fish", "{}")
    assert added.exit_code == 0, added.output
    packages = _shelf(_isolated_config)["sources"]["main"]["packages"]  # type: ignore[index]
    assert packages == {"git": {"policy": "once"}, "fish": {}}
    assert _get("shelf.sources.main.packages.fish")["value"] == {"enabled": True, "policy": None}

    removed = _config("unset", "shelf.sources.main.packages.fish")
    assert removed.exit_code == 0, removed.output
    packages = _shelf(_isolated_config)["sources"]["main"]["packages"]  # type: ignore[index]
    assert packages == {"git": {"policy": "once"}}


def test_unsetting_the_last_setting_of_an_entry_keeps_the_entry(_isolated_config: Path) -> None:
    """The entry existing is a choice of its own (a package that is on)."""
    result = _config("unset", "shelf.sources.main.packages.git.policy")
    assert result.exit_code == 0, result.output
    packages = _shelf(_isolated_config)["sources"]["main"]["packages"]  # type: ignore[index]
    assert packages == {"git": {}}


def test_a_setting_under_a_missing_entry_is_unset_not_its_default() -> None:
    got = _get("shelf.sources.main.packages.zsh.enabled")
    assert (got["value"], got["source"]) == (None, "unset")


def test_a_setting_under_a_missing_entry_is_validated_by_its_section(
    _isolated_config: Path,
) -> None:
    before = _isolated_config.read_text(encoding="utf-8")
    result = _config("set", "shelf.sources.home.packages.fish.policy", "sync")
    assert result.exit_code != 0
    assert "invalid value for 'shelf.sources.home.packages.fish.policy'" in result.stderr
    assert "needs url" in result.stderr
    assert _isolated_config.read_text(encoding="utf-8") == before

    created = _config("set", "shelf.sources.home.url", "https://git.example.com/home.git")
    assert created.exit_code == 0, created.output
    assert _shelf(_isolated_config)["sources"]["home"] == {  # type: ignore[index]
        "url": "https://git.example.com/home.git"
    }


def test_an_entry_value_is_checked_against_its_type() -> None:
    result = _config("set", "shelf.sources.main.packages.git.policy", "always")
    assert result.exit_code != 0
    assert "invalid value" in result.stderr


def test_a_scalar_map_takes_entries_too(_isolated_config: Path) -> None:
    result = _config("set", "shelf.labels.team", "core")
    assert result.exit_code == 0, result.output
    assert _shelf(_isolated_config)["labels"] == {"Upper": "kept", "team": "core"}


def test_a_map_of_maps_keeps_the_outer_entry_when_its_last_inner_one_goes(
    _isolated_config: Path,
) -> None:
    assert _config("set", "shelf.groups.ops.lead", "ana").exit_code == 0
    assert _shelf(_isolated_config)["groups"] == {"ops": {"lead": "ana"}}
    removed = _config("unset", "shelf.groups.ops.lead")
    assert removed.exit_code == 0, removed.output
    assert _shelf(_isolated_config)["groups"] == {"ops": {}}


# ── names a key can address ──────────────────────────────────────────────────


def test_a_capital_in_an_entry_name_is_refused_and_names_the_whole_map() -> None:
    result = _config("set", "shelf.labels.Upper", "x")
    assert result.exit_code == 2, result.output
    assert "shelf.labels entry 'Upper' can't be named in a key" in result.stderr
    assert "config set shelf.labels VALUE" in result.stderr


def test_a_map_with_an_upper_case_key_is_still_set_whole(_isolated_config: Path) -> None:
    result = _config("set", "shelf.labels", '{"Upper": "changed"}')
    assert result.exit_code == 0, result.output
    assert _shelf(_isolated_config)["labels"] == {"Upper": "changed"}


def test_a_key_past_a_value_inside_an_entry_is_not_found() -> None:
    result = _config("set", "shelf.sources.main.files.policy", "sync")
    assert result.exit_code == 1, result.output
    assert "setting not found: 'shelf.sources.main.files.policy'" in result.stderr


def test_config_list_keeps_one_row_per_map() -> None:
    result = _config("list", "-f", "json")
    assert result.exit_code == 0, result.output
    keys = [row["key"] for row in json.loads(result.stdout) if row["key"].startswith("shelf.")]
    assert keys == [
        "shelf.sources",
        "shelf.labels",
        "shelf.tokens",
        "shelf.trial",
        "shelf.groups",
        "shelf.servers",
    ]


def test_an_entry_has_its_maps_stability() -> None:
    assert _get("shelf.trial.one")["stability"] == "experimental"
    assert _get("shelf.trial.one.policy")["stability"] == "experimental"
    assert _get("shelf.labels.team")["stability"] == "stable"


# ── marks, profiles and projects ─────────────────────────────────────────────


def test_a_home_only_maps_entry_is_refused_in_a_profile(_isolated_config: Path) -> None:
    before = _isolated_config.read_text(encoding="utf-8")
    result = _config("set", "shelf.sources.main.packages.git.policy", "sync", "--profile", "work")
    assert result.exit_code == 4, result.output
    assert "shelf.sources.main.packages.git.policy can't be set in profile work" in result.stderr
    assert _isolated_config.read_text(encoding="utf-8") == before


@pytest.mark.usefixtures("home_run")
@pytest.mark.parametrize(
    ("key_", "value"),
    [("shelf.sources.main.packages.git.policy", "sync"), ("shelf.tokens.ci", "s3cret")],
)
def test_a_home_only_or_secret_entry_is_refused_in_a_projects_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key_: str, value: str
) -> None:
    root = make_project(tmp_path / "PROJ-1").root
    monkeypatch.chdir(root)
    forget_folder_layer()
    try:
        app = bootstrap.build_root_app(
            candidates=[plugin_candidate(make_plugin("shelf", settings=ShelfSettings))]
        ).meta
        result = invoke_cli(app, ["config", "set", key_, value, "--project"])
    finally:
        forget_folder_layer()
    assert result.exit_code == 4, result.output
    assert f"{key_} can't be set in" in result.stderr
    assert not (root / ".untaped" / "config.yml").exists()


def test_a_secret_entry_is_masked() -> None:
    assert _config("set", "shelf.tokens.ci", "s3cret").exit_code == 0
    assert _get("shelf.tokens.ci")["value"] == "***"


# ── variables ────────────────────────────────────────────────────────────────


def test_a_variable_sets_one_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    name = "UNTAPED_SHELF__SOURCES__MAIN__PACKAGES__GIT__POLICY"
    assert env_var_name(("shelf", "sources", "main", "packages", "git", "policy")) == name
    monkeypatch.setenv(name, "manual")
    got = _get("shelf.sources.main.packages.git.policy")
    assert (got["value"], got["source"]) == ("manual", "env")


def test_an_entry_name_keeps_its_hyphen_in_its_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings source reads the entry name as written; only a section's hyphen turns."""
    name = env_var_name(("shelf", "labels", "my-team"))
    assert name == "UNTAPED_SHELF__LABELS__MY-TEAM"
    assert env_var_name(("acme-tools", "greeting")) == "UNTAPED_ACME_TOOLS__GREETING"
    monkeypatch.setenv(name, "core")
    got = _get("shelf.labels.my-team")
    assert (got["value"], got["source"]) == ("core", "env")


# ── key() ────────────────────────────────────────────────────────────────────


def test_key_names_an_entry_and_a_setting_inside_it() -> None:
    setting = key(ShelfSettings).sources["main"].packages["git"].policy
    assert str(setting) == "sources.main.packages.git.policy"
    assert SettingKey.path_of(setting) == ("sources", "main", "packages", "git", "policy")
    assert str(key(ShelfSettings).labels["team"]) == "labels.team"
    loaded = ShelfSettings.model_validate(yaml.safe_load(HOME)["shelf"])
    assert value_in(setting, loaded) == "once"
    assert value_in(key(ShelfSettings).sources["home"].url, loaded) is None


def test_key_typos_fail_where_they_are_written() -> None:
    with pytest.raises(AttributeError, match="has no field 'pkgs'"):
        _ = key(ShelfSettings).sources["main"].pkgs
    with pytest.raises(AttributeError, match="is a value, not a model"):
        _ = key(ShelfSettings).sources.url  # a map steps by entry, not by field
    with pytest.raises(AttributeError, match="is a value, not a model"):
        _ = key(ShelfSettings).labels["team"].text
    with pytest.raises(TypeError, match="is not a map setting"):
        _ = key(ShelfSettings).sources["main"].url["x"]
    with pytest.raises(ValueError, match="can't be named in a key"):
        _ = key(ShelfSettings).labels["Team"]
    with pytest.raises(ValueError, match="can't be named in a key"):
        _ = key(ShelfSettings).labels["a.b"]


# ── a map that declares its names ────────────────────────────────────────────


def test_an_entry_of_a_map_with_declared_names_is_checked_on_write(
    _isolated_config: Path,
) -> None:
    before = _isolated_config.read_text(encoding="utf-8")
    result = _config("set", "untaped.ui.symbols.nosuch", "Y")
    assert result.exit_code != 0
    assert "unknown name nosuch" in result.stderr
    assert _isolated_config.read_text(encoding="utf-8") == before
    assert _config("set", "untaped.ui.symbols.success", "Y").exit_code == 0
    assert read_config_dict(_isolated_config)["untaped"] == {"ui": {"symbols": {"success": "Y"}}}


def test_an_unset_entry_of_a_map_with_declared_names_shows_its_default() -> None:
    themed = _config("set", "untaped.ui.theme", "classic")
    assert themed.exit_code == 0, themed.output
    got = _get("untaped.ui.symbols.heading")
    assert (got["value"], got["source"]) == ("◆", "default")


# ── found in review ──────────────────────────────────────────────────────────


def test_an_entry_set_to_nothing_is_read_from_where_it_is_written(_isolated_config: Path) -> None:
    assert _config("set", "shelf.sources.main.packages.fish", "{}").exit_code == 0
    got = _get("shelf.sources.main.packages.fish")
    assert (got["source"], got["profile"]) == ("base", DEFAULT_PROFILE)


def test_an_entry_of_a_renamed_map_carries_the_map_to_its_name(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nshelf: {old_groups: {ops: {lead: ana}}}\n")
    result = _config("set", "shelf.groups.dev.lead", "bo")
    assert result.exit_code == 0, result.output
    shelf = _shelf(_isolated_config)
    assert "old_groups" not in shelf
    assert shelf["groups"] == {"ops": {"lead": "ana"}, "dev": {"lead": "bo"}}
    assert _get("shelf.groups.ops.lead")["value"] == "ana"


def test_a_secret_inside_a_map_is_written_as_given(_isolated_config: Path) -> None:
    whole = _config("set", "shelf.tokens", '{"ci": "s3cret"}')
    assert whole.exit_code == 0, whole.output
    entry = _config("set", "shelf.servers.main", '{"url": "https://x.example.com", "token": "t0k"}')
    assert entry.exit_code == 0, entry.output
    shelf = _shelf(_isolated_config)
    assert shelf["tokens"] == {"ci": "s3cret"}
    assert shelf["servers"] == {"main": {"url": "https://x.example.com", "token": "t0k"}}


def test_a_map_of_models_set_whole_keeps_only_what_was_given(_isolated_config: Path) -> None:
    result = _config("set", "shelf.trial", '{"one": {"policy": "sync"}}')
    assert result.exit_code == 0, result.output
    assert _shelf(_isolated_config)["trial"] == {"one": {"policy": "sync"}}


@pytest.mark.usefixtures("home_run")
@pytest.mark.parametrize(
    ("key_", "value"),
    [
        ("shelf.tokens", '{"ci": "s"}'),
        ("shelf.servers.main", '{"url": "https://x.example.com", "token": "s"}'),
        ("shelf.servers", '{"main": {"url": "https://x.example.com"}}'),
    ],
)
def test_a_map_or_entry_that_holds_a_secret_is_refused_in_a_projects_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key_: str, value: str
) -> None:
    root = make_project(tmp_path / "PROJ-1").root
    monkeypatch.chdir(root)
    forget_folder_layer()
    try:
        app = bootstrap.build_root_app(
            candidates=[plugin_candidate(make_plugin("shelf", settings=ShelfSettings))]
        ).meta
        result = invoke_cli(app, ["config", "set", key_, value, "--project"])
    finally:
        forget_folder_layer()
    assert result.exit_code == 4, result.output
    assert "it is a secret" in result.stderr


def test_a_stray_declared_name_never_blocks_another_entry_or_its_own_removal(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config, "format_version: 2\nuntaped: {ui: {symbols: {old1: a, old2: b}}}\n"
    )
    assert _config("set", "untaped.ui.symbols.success", "Y").exit_code == 0
    removed = _config("unset", "untaped.ui.symbols.old1")
    assert removed.exit_code == 0, removed.output
    symbols = read_config_dict(_isolated_config)["untaped"]["ui"]["symbols"]
    assert symbols == {"old2": "b", "success": "Y"}


def test_a_name_starting_with_a_hyphen_is_refused_saying_so() -> None:
    result = _config("set", "shelf.labels.-x", "y")
    assert result.exit_code == 2, result.output
    assert "not starting with '-'" in result.stderr


def test_a_dotted_name_in_a_map_of_values_points_to_the_whole_map() -> None:
    result = _config("set", "untaped.ui.symbols.check.fail", "x")
    assert result.exit_code == 1, result.output
    assert "config set untaped.ui.symbols VALUE" in result.stderr


def test_a_dir_placed_at_a_map_entry_is_refused() -> None:
    stray = make_plugin(
        "stray", settings=ShelfSettings, data=Dir(at=key(ShelfSettings).labels["x"])
    )
    result = bootstrap.compose_root(candidates=[plugin_candidate(stray)])
    [refused] = result.quarantine
    assert "an entry of a map" in refused.detail


def test_a_table_column_is_never_a_map_entry() -> None:
    class Repo(BaseModel):
        name: str
        labels: dict[str, str] = {}

    assert _column_name(key(Repo).name) == "name"
    with pytest.raises(TypeError, match="not a map entry"):
        _column_name(key(Repo).labels["name"])


def test_only_a_union_is_looked_into_for_a_model() -> None:
    assert model_in(Server | None) is Server
    assert model_in(list[Server]) is None
    assert model_in(dict[str, Server]) is None
