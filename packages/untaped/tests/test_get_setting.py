from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from untaped.config import GetSetting, SettingsFileRepository
from untaped.config_schema import Choices
from untaped.deprecated_keys import Renamed
from untaped.errors import ConfigError
from untaped.settings import (
    get_settings,
    register_profile_settings,
)


class DemoPluginSettings(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None
    default_token: SecretStr = SecretStr("default-secret")


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The testing plugin resets the registry and settings cache around each test.
    cfg = tmp_path / "config.yml"
    register_profile_settings("demo", DemoPluginSettings)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


@pytest.mark.parametrize(
    ("config", "key", "value", "default", "source", "profile"),
    [
        # Profile-scoped fields are only effective under ``profiles.<name>``;
        # the resolved source names the profile that supplied the value.
        (
            "profiles:\n  default:\n    skills:\n      updates: auto\n",
            "skills.updates",
            "auto",
            "warn",
            "profile:default",
            "default",
        ),
        (
            "profiles:\n  default:\n    skills:\n      updates: warn\n"
            "  stage:\n    skills:\n      updates: auto\nactive: stage\n",
            "skills.updates",
            "auto",
            "warn",
            "profile:stage",
            "stage",
        ),
        (
            "profiles:\n  default:\n    ui:\n      theme: classic\n",
            "ui.theme",
            "classic",
            "default",
            "profile:default",
            "default",
        ),
        ("", "ui.theme", "default", "default", "default", None),
        (
            "profiles:\n  default:\n    ui:\n      color_roles:\n        error: red\n",
            "ui.color_roles",
            {"error": "red"},
            {},
            "profile:default",
            "default",
        ),
        (
            "profiles:\n  default:\n    http:\n      verify_ssl: false\n",
            "http.verify_ssl",
            False,
            True,
            "profile:default",
            "default",
        ),
        ("", "http.verify_ssl", True, True, "default", None),
    ],
    ids=[
        "default-profile",
        "active-profile",
        "ui",
        "ui-default",
        "ui-mapping",
        "http",
        "http-default",
    ],
)
def test_get_returns_effective_value_default_source_and_profile(
    _isolate_settings: Path,
    config: str,
    key: str,
    value: object,
    default: object,
    source: str,
    profile: str | None,
) -> None:
    _isolate_settings.write_text(config)

    entry = GetSetting(SettingsFileRepository())(key)

    assert (entry.key, entry.value, entry.default) == (key, value, default)
    assert entry.source.label == source
    assert entry.profile == profile


def test_get_honours_environment_override(
    _isolate_settings: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    skills:\n      updates: warn\n")
    monkeypatch.setenv("UNTAPED_SKILLS__UPDATES", "off")
    get_settings.cache_clear()

    entry = GetSetting(SettingsFileRepository())("skills.updates")

    assert entry.value == "off"
    assert entry.source.label == "env"
    assert entry.profile is None


@pytest.mark.parametrize(
    ("key", "reveal", "value", "default"),
    [
        ("demo.token", False, "***", None),
        ("demo.token", True, "secret-token", None),
        # A secret *default* follows the same reveal gate.
        ("demo.default_token", False, "***", "***"),
        ("demo.default_token", True, "default-secret", "default-secret"),
    ],
)
def test_get_secrets_follow_the_reveal_gate(
    _isolate_settings: Path, key: str, reveal: bool, value: str, default: str | None
) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    demo:\n      token: secret-token\n")

    entry = GetSetting(SettingsFileRepository())(key, reveal_secrets=reveal)

    assert entry.value == value
    assert entry.default == default


def test_get_unknown_profile_setting_is_rejected(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        GetSetting(SettingsFileRepository())("plugins.tool.spec")


_SYMBOLS = (
    "profiles:\n  default:\n    ui: {theme: plain, symbols: {error: X, bogus: y}}\n"
    "  work:\n    ui: {symbols: {success: S}}\nactive: work\n"
)


def test_list_names_gives_each_declared_name_its_value_and_source(_isolate_settings: Path) -> None:
    from untaped.config.use_cases import ListNames
    from untaped.theme import BUILTIN_THEMES, SYMBOL_NAMES

    _isolate_settings.write_text(_SYMBOLS)

    names = {item.name: item for item in ListNames(SettingsFileRepository())("ui.symbols")}

    assert list(names)[: len(SYMBOL_NAMES)] == list(SYMBOL_NAMES)
    assert (names["success"].value, names["success"].source) == ("S", "profile:work")
    assert (names["error"].value, names["error"].source) == ("X", "profile:default")
    plain = BUILTIN_THEMES["plain"].symbols["warning"]  # the theme the profiles chose
    assert (names["warning"].value, names["warning"].source) == (plain, "theme")
    assert (names["bogus"].source, names["bogus"].declared) == ("profile:default", False)


def test_list_names_says_which_names_a_variable_sets(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.config.use_cases import ListNames

    _isolate_settings.write_text(_SYMBOLS)
    monkeypatch.setenv("UNTAPED_UI__SYMBOLS", '{"info": "i"}')

    names = {item.name: item for item in ListNames(SettingsFileRepository())("ui.symbols")}

    assert (names["info"].value, names["info"].source) == ("i", "UNTAPED_UI__SYMBOLS")
    assert names["success"].source == "profile:work"  # the variable is merged over the profiles


def test_list_names_refuses_a_key_that_declares_none() -> None:
    from untaped.config.use_cases import ListNames

    with pytest.raises(ConfigError, match="declares no names"):
        ListNames(SettingsFileRepository())("ui.theme")


def test_list_names_under_a_profile_shows_what_its_own_names_inherit(
    _isolate_settings: Path,
) -> None:
    from untaped.config.use_cases import ListNames
    from untaped.theme import BUILTIN_THEMES

    _isolate_settings.write_text(
        "profiles:\n  default:\n    ui: {symbols: {error: X}}\n"
        "  work:\n    ui: {symbols: {success: S, error: E}}\nactive: work\n"
    )

    names = {
        item.name: item for item in ListNames(SettingsFileRepository())("ui.symbols", under="work")
    }

    assert (names["error"].value, names["error"].source) == ("X", "profile:default")
    theme = BUILTIN_THEMES["default"].symbols["success"]
    assert (names["success"].value, names["success"].source) == (theme, "theme")


@pytest.mark.parametrize(
    ("variable", "value"),
    [("UNTAPED_UI__SYMBOLS__SUCCESS", "W"), ("UNTAPED_UI", '{"symbols": {"success": "W"}}')],
)
def test_list_names_finds_a_variable_at_any_depth(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    from untaped.config.use_cases import ListNames

    _isolate_settings.write_text(_SYMBOLS)
    monkeypatch.setenv(variable, value)

    names = {
        item.name: item for item in ListNames(SettingsFileRepository())("ui.symbols", under="work")
    }

    assert (names["success"].value, names["success"].source) == ("W", variable)
    assert names["success"].variable == variable  # it wins over the profile's own ``S``
    assert names["error"].source == "profile:default"


def test_list_names_leaves_a_name_its_defaults_lack_unset(_isolate_settings: Path) -> None:
    from untaped.config.use_cases import ListNames

    _isolate_settings.write_text("profiles:\n  default: {}\n")

    names = {item.name: item for item in ListNames(SettingsFileRepository())("ui.color_roles")}

    assert (names["header"].value, names["header"].source) == (None, "theme")
    assert (names["success"].value, names["success"].source) == ("green", "theme")


class MarksPlugin(BaseModel):
    renamed_keys: ClassVar[Mapping[str, object]] = {"old_marks": Renamed("marks", removed_in=12)}

    marks: Annotated[dict[str, str], Choices(lambda: ("a", "b"), "marks")] = {}


@pytest.mark.parametrize(
    ("variable", "value"),
    [("UNTAPED_MARKS__OLD_MARKS__A", "E"), ("UNTAPED_MARKS", '{"old_marks": {"a": "E"}}')],
)
def test_list_names_shows_a_value_an_old_spelling_sets(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    from untaped.config.use_cases import ListNames

    register_profile_settings("marks", MarksPlugin)
    monkeypatch.setenv(variable, value)

    names = {item.name: item for item in ListNames(SettingsFileRepository())("marks.marks")}

    assert names["a"].value == "E" and names["a"].variable is not None


@pytest.mark.parametrize(
    ("value", "found"),
    [
        ('{"symbols": {"info": "i"}}', True),
        ('{"symbols": {}}', False),  # the object doesn't hold it
        ('{"symbols": "x"}', True),  # not an object: can't be told apart
        ("nope", True),
    ],
)
def test_env_var_setting_finds_the_deepest_variable_holding_a_leaf(
    monkeypatch: pytest.MonkeyPatch, value: str, found: bool
) -> None:
    from untaped.settings import env_var_setting

    monkeypatch.setenv("UNTAPED_UI", value)

    assert env_var_setting(("ui", "symbols", "info")) == ("UNTAPED_UI" if found else None)
