from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, SecretStr

from untaped.config import ListAllProfilesSettings, ListSettings, SettingsFileRepository, Source
from untaped.settings import (
    get_settings,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
)


class DemoProfileSettings(BaseModel):
    directory: Path = Path("~/.demo")
    token: SecretStr | None = None
    api_prefix: str = "/api/demo/v1/"
    default_scope: str | None = None
    page_size: int = 200
    default_token: SecretStr = SecretStr("default-secret")


class DemoStateSettings(BaseModel):
    entries: list[str] = Field(default_factory=list)


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    reset_config_registry_for_tests()
    register_profile_settings("demo", DemoProfileSettings)
    register_state_settings("demo", DemoStateSettings)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("config", "env", "key", "source", "value"),
    [
        ("", None, "demo.token", Source(kind="unset"), None),
        ("", None, "log_level", Source(kind="default"), "INFO"),
        ("", None, "demo.api_prefix", Source(kind="default"), "/api/demo/v1/"),
        ("", None, "demo.directory", Source(kind="default"), "~/.demo"),
        # Profile-scoped values are attributed to the supplying profile.
        (
            "profiles:\n  default:\n    log_level: DEBUG\n",
            None,
            "log_level",
            Source(kind="profile", profile="default"),
            "DEBUG",
        ),
        (
            "profiles:\n  default:\n    log_level: INFO\n  prod:\n    log_level: WARNING\n"
            "active: prod\n",
            None,
            "log_level",
            Source(kind="profile", profile="prod"),
            "WARNING",
        ),
        (
            "profiles:\n  default:\n    http:\n      verify_ssl: false\n",
            None,
            "http.verify_ssl",
            Source(kind="profile", profile="default"),
            False,
        ),
        ("log_level: DEBUG\n", "WARNING", "log_level", Source(kind="env"), "WARNING"),
    ],
    ids=[
        "unset",
        "core-default",
        "capability-default",
        "path-default",
        "default-profile",
        "active-profile",
        "http",
        "env",
    ],
)
def test_list_attributes_each_value_to_its_source(
    _isolate_settings: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: str,
    env: str | None,
    key: str,
    source: Source,
    value: object,
) -> None:
    _isolate_settings.write_text(config)
    if env is not None:
        monkeypatch.setenv("UNTAPED_LOG_LEVEL", env)
    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    assert entries[key].source == source
    assert entries[key].value == value


@pytest.mark.parametrize("reveal", [False, True])
def test_list_secrets_follow_the_reveal_gate(_isolate_settings: Path, reveal: bool) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    demo:\n      token: super-secret\n")
    entries = {e.key: e for e in ListSettings(SettingsFileRepository())(reveal_secrets=reveal)}
    assert entries["demo.token"].value == ("super-secret" if reveal else "***")
    default_token = entries["demo.default_token"]
    assert default_token.value == default_token.default == ("default-secret" if reveal else "***")


def test_collection_fields_skipped() -> None:
    entries = {e.key for e in ListSettings(SettingsFileRepository())()}
    # ``demo.entries`` is a list — collections are skipped, scalar siblings are not.
    assert "demo.entries" not in entries
    assert {"demo.directory", "demo.default_scope", "demo.page_size"} <= entries


@pytest.mark.parametrize(
    ("key", "env_var"), [("log_level", "UNTAPED_LOG_LEVEL"), ("demo.token", "UNTAPED_DEMO__TOKEN")]
)
def test_env_var_naming(key: str, env_var: str) -> None:
    repo = SettingsFileRepository()
    assert repo.env_var_for(repo.descriptor(key)) == env_var


def test_all_profiles_shows_one_row_per_profile_and_key(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    log_level: DEBUG\n    demo:\n      page_size: 50\n"
        "active: prod\n"
    )

    entries = ListAllProfilesSettings(SettingsFileRepository())()
    rows = {(e.profile, e.key, e.value) for e in entries}
    assert rows == {
        ("default", "log_level", "INFO"),
        ("prod", "log_level", "DEBUG"),
        ("prod", "demo.page_size", 50),
    }
    assert all(e.source.kind == "profile" for e in entries)
