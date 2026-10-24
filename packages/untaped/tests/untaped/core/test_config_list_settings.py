from pathlib import Path

import pytest
from pydantic import BaseModel, Field, SecretStr

from untaped.core.config import (
    ListAllProfilesSettings,
    ListSettings,
    SettingsFileRepository,
    Source,
)
from untaped.runtime.settings import (
    register_profile_settings,
    register_state_settings,
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
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    register_profile_settings("demo", DemoProfileSettings)
    register_state_settings("demo", DemoStateSettings)
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    return cfg


@pytest.mark.parametrize(
    ("config", "env", "key", "source", "value"),
    [
        ("", None, "demo.token", Source(kind="unset"), None),
        ("", None, "untaped.skills.updates", Source(kind="default"), "auto"),
        ("", None, "demo.api_prefix", Source(kind="default"), "/api/demo/v1/"),
        ("", None, "demo.directory", Source(kind="default"), "~/.demo"),
        # Profile-scoped values are attributed to the supplying profile.
        (
            "format_version: 2\nuntaped:\n  skills:\n    updates: warn\n",
            None,
            "untaped.skills.updates",
            Source(kind="profile", profile="default"),
            "warn",
        ),
        (
            "format_version: 2\nuntaped:\n  skills:\n    updates: auto\n"
            "profiles:\n  prod:\n    untaped:\n      skills:\n        updates: warn\n",
            None,
            "untaped.skills.updates",
            Source(kind="profile", profile="prod"),
            "warn",
        ),
        (
            "format_version: 2\nuntaped:\n  http:\n    timeout_seconds: 5\n",
            None,
            "untaped.http.timeout_seconds",
            Source(kind="profile", profile="default"),
            5.0,
        ),
        (
            "format_version: 2\nuntaped:\n  skills:\n    updates: auto\n",
            "off",
            "untaped.skills.updates",
            Source(kind="env", variable="UNTAPED_UNTAPED__SKILLS__UPDATES"),
            "off",
        ),
    ],
    ids=[
        "unset",
        "core-default",
        "plugin-default",
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
        monkeypatch.setenv("UNTAPED_UNTAPED__SKILLS__UPDATES", env)
    if source.profile not in (None, "default"):
        monkeypatch.setenv("UNTAPED_PROFILE", source.profile)
    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    assert entries[key].source == source
    assert entries[key].value == value


@pytest.mark.parametrize("reveal", [False, True])
def test_list_secrets_follow_the_reveal_gate(_isolate_settings: Path, reveal: bool) -> None:
    _isolate_settings.write_text("format_version: 2\ndemo:\n  token: super-secret\n")
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
    ("key", "env_var"),
    [
        ("untaped.skills.updates", "UNTAPED_UNTAPED__SKILLS__UPDATES"),
        ("demo.token", "UNTAPED_DEMO__TOKEN"),
    ],
)
def test_env_var_naming(key: str, env_var: str) -> None:
    repo = SettingsFileRepository()
    assert repo.env_var_for(repo.descriptor(key)) == env_var


def test_all_profiles_shows_one_row_per_profile_and_key(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_settings.write_text(
        "format_version: 2\n"
        "untaped:\n  skills:\n    updates: warn\n"
        "profiles:\n  prod:\n    untaped:\n      skills:\n        updates: auto\n"
        "    demo:\n      page_size: 50\n"
        ""
    )
    monkeypatch.setenv("UNTAPED_PROFILE", "prod")

    entries = ListAllProfilesSettings(SettingsFileRepository())()
    rows = {(e.profile, e.key, e.value) for e in entries}
    assert rows == {
        ("default", "untaped.skills.updates", "warn"),
        ("prod", "untaped.skills.updates", "auto"),
        ("prod", "demo.page_size", 50),
    }
    assert all(e.source.kind == "profile" for e in entries)


def test_each_bad_part_of_core_s_section_is_reported_and_the_rest_validated(
    _isolate_settings: Path,
) -> None:
    _isolate_settings.write_text(
        "format_version: 2\nuntaped:\n  http: {timeout_seconds: -1}\n  ui: {border: bogus}\n"
    )
    listing = ListSettings(SettingsFileRepository())
    entries = {e.key: e for e in listing()}
    assert "timeout_seconds" in listing.errors["untaped"]
    assert "border" in listing.errors["untaped"]
    assert entries["untaped.skills.updates"].value == "auto"
