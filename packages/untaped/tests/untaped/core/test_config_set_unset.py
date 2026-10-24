from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import SettingsConfigDict

from untaped.core.config import SettingsFileRepository
from untaped.runtime.settings import (
    Settings,
    register_profile_settings,
)
from untaped.sdk.errors import ConfigError


class DemoPluginSettings(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None
    page_size: int = 200


class StrictSettings(Settings):
    """No real setting is required-without-default today; this one is, so the
    post-unset validation can be exercised."""

    model_config = SettingsConfigDict(
        env_prefix="UNTAPED_", env_nested_delimiter="__", extra="ignore"
    )
    mandatory: str = Field(...)


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The testing plugin resets the registry and settings cache around each test.
    cfg = tmp_path / "config.yml"
    register_profile_settings("demo", DemoPluginSettings)
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    return cfg


def _write(cfg: Path, text: str | None) -> None:
    if text is not None:
        cfg.write_text(text)


# ── set ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("initial", "key", "value", "profile", "written_to", "expected"),
    [
        (
            None,
            "untaped.skills.updates",
            "auto",
            None,
            "default",
            {"default": {"untaped": {"skills": {"updates": "auto"}}}},
        ),
        (None, "demo.token", "ghp_x", None, "default", {"default": {"demo": {"token": "ghp_x"}}}),
        # YAML scalars are coerced; ``http`` is an ordinary per-profile setting.
        (
            None,
            "untaped.http.verify_ssl",
            "false",
            None,
            "default",
            {"default": {"untaped": {"http": {"verify_ssl": False}}}},
        ),
        (
            None,
            "untaped.ui.theme",
            "classic",
            None,
            "default",
            {"default": {"untaped": {"ui": {"theme": "classic"}}}},
        ),
        (
            None,
            "untaped.ui.collection_view",
            "list",
            None,
            "default",
            {"default": {"untaped": {"ui": {"collection_view": "list"}}}},
        ),
        # Writing the default scope creates it and leaves other profiles intact.
        (
            "profiles:\n  prod:\n    untaped:\n      skills:\n        updates: warn\n",
            "untaped.skills.updates",
            "auto",
            None,
            "default",
            {
                "prod": {"untaped": {"skills": {"updates": "warn"}}},
                "default": {"untaped": {"skills": {"updates": "auto"}}},
            },
        ),
        (
            "profiles:\n  default: {}\n  prod: {}\nactive: prod\n",
            "untaped.skills.updates",
            "auto",
            None,
            "prod",
            {"default": {}, "prod": {"untaped": {"skills": {"updates": "auto"}}}},
        ),
        (
            "profiles:\n  default: {}\n  work: {}\n",
            "untaped.http.proxy",
            "http://p:8080",
            "work",
            "work",
            {"default": {}, "work": {"untaped": {"http": {"proxy": "http://p:8080"}}}},
        ),
    ],
    ids=[
        "core",
        "nested",
        "coerced",
        "ui",
        "ui-literal",
        "creates-default",
        "active-profile",
        "named-profile",
    ],
)
def test_set_writes_into_the_target_profile(
    _isolate_settings: Path,
    initial: str | None,
    key: str,
    value: str,
    profile: str | None,
    written_to: str,
    expected: dict[str, Any],
) -> None:
    _write(_isolate_settings, initial)
    assert SettingsFileRepository().set_value(key, value, profile=profile) == written_to
    assert yaml.safe_load(_isolate_settings.read_text())["profiles"] == expected


def test_set_preserves_other_keys_and_state(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n"
        "    untaped:\n      skills:\n        updates: auto\n"
        "    demo:\n      base_url: https://prod\n"
        "workspace:\n  active:\n    - name: ws1\n"
        '      created_at: "2026-10-01T00:00:00Z"\n      repos: []\n'
    )
    SettingsFileRepository().set_value("demo.token", "tok")
    data = yaml.safe_load(_isolate_settings.read_text())
    default = data["profiles"]["default"]
    assert default["untaped"]["skills"] == {"updates": "auto"}
    assert default["demo"] == {"base_url": "https://prod", "token": "tok"}
    # Unknown top-level keys are untouched by a profile write.
    assert data["workspace"]["active"][0]["name"] == "ws1"


@pytest.mark.parametrize(
    ("initial", "key", "value", "profile", "match"),
    [
        (None, "untaped.http.verify_ssl", "not-a-bool", None, "verify_ssl"),
        (None, "bogus.key", "x", None, "unknown setting"),
        (None, "plugins.tool.spec", "untaped", None, "unknown setting"),
        (None, "untaped.skills.updates", "auto", "prod", "profile not found.*prod"),
        ("profiles:\n  default: {}\n", "untaped.skills.updates", "auto", "ghost", "ghost"),
        (
            "profiles:\n  default:\n    untaped:\n      ui:\n        theme: classic\n",
            "untaped.ui.collection_view",
            "nope",
            None,
            "invalid value",
        ),
        # Validation merges from the *target* profile's perspective, so an
        # invalid value can't land in a non-active profile unnoticed.
        (
            "profiles:\n  default:\n    demo:\n      page_size: 50\n  stage: {}\nactive: default\n",
            "demo.page_size",
            "abc",
            "stage",
            "invalid value",
        ),
    ],
    ids=[
        "invalid-value",
        "unknown-key",
        "state-key",
        "unknown-profile",
        "unknown-named-profile",
        "invalid-literal",
        "invalid-in-target-profile",
    ],
)
def test_set_rejects_without_writing(
    _isolate_settings: Path,
    initial: str | None,
    key: str,
    value: str,
    profile: str | None,
    match: str,
) -> None:
    _write(_isolate_settings, initial)
    with pytest.raises(ConfigError, match=match) as excinfo:
        SettingsFileRepository().set_value(key, value, profile=profile)
    assert "untaped-profile" not in str(excinfo.value)
    if initial is None:
        assert not _isolate_settings.exists()
    else:
        assert _isolate_settings.read_text() == initial


def test_set_validation_isolated_from_env_overlay(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (#136): validation judges the written YAML alone, so a
    valid env var can't mask an invalid value landing on disk (which would
    break ``get_settings()`` the day the env var goes away)."""
    monkeypatch.setenv("UNTAPED_UNTAPED__HTTP__VERIFY_SSL", "true")
    with pytest.raises(ConfigError, match="verify_ssl"):
        SettingsFileRepository().set_value("untaped.http.verify_ssl", "not-a-bool-or-anything")


# ── unset ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("initial", "key", "profile", "removed", "expected"),
    [
        (
            "profiles:\n  default:\n    untaped:\n      skills:\n        updates: auto\n"
            "    demo:\n      base_url: https://x\n",
            "untaped.skills.updates",
            None,
            True,
            {"default": {"demo": {"base_url": "https://x"}}},
        ),
        # An emptied parent mapping is cleaned up; siblings are kept.
        (
            "profiles:\n  default:\n    untaped:\n      skills:\n        updates: auto\n"
            "    demo:\n      token: x\n",
            "demo.token",
            None,
            True,
            {"default": {"untaped": {"skills": {"updates": "auto"}}}},
        ),
        (
            "profiles:\n  default:\n    demo:\n      token: x\n      base_url: https://y\n",
            "demo.token",
            None,
            True,
            {"default": {"demo": {"base_url": "https://y"}}},
        ),
        (
            "profiles:\n  default:\n    untaped:\n      skills:\n        updates: auto\n"
            "      ui:\n        theme: classic\n",
            "untaped.ui.theme",
            None,
            True,
            {"default": {"untaped": {"skills": {"updates": "auto"}}}},
        ),
        # The schema default fills the gap, so the merged dict stays valid.
        (
            "profiles:\n  default:\n    demo:\n      page_size: 50\n",
            "demo.page_size",
            None,
            True,
            {"default": {}},
        ),
        (
            "profiles:\n  default:\n    untaped:\n      skills:\n        updates: warn\n"
            "  prod:\n    untaped:\n      skills:\n        updates: auto\nactive: prod\n",
            "untaped.skills.updates",
            "default",
            True,
            {"default": {}, "prod": {"untaped": {"skills": {"updates": "auto"}}}},
        ),
        ("profiles:\n  default: {}\n", "untaped.skills.updates", None, False, {"default": {}}),
    ],
    ids=[
        "core",
        "cleans-empty-parent",
        "keeps-siblings",
        "ui",
        "schema-default",
        "named-profile",
        "not-set",
    ],
)
def test_unset_removes_the_key(
    _isolate_settings: Path,
    initial: str,
    key: str,
    profile: str | None,
    removed: bool,
    expected: dict[str, Any],
) -> None:
    _isolate_settings.write_text(initial)
    assert SettingsFileRepository().unset_value(key, profile=profile) == (
        removed,
        profile or "default",
    )
    assert yaml.safe_load(_isolate_settings.read_text())["profiles"] == expected


def test_unset_rejects_unknown_target_profile(_isolate_settings: Path) -> None:
    original = "profiles:\n  default:\n    untaped:\n      skills:\n        updates: auto\n"
    _isolate_settings.write_text(original)
    with pytest.raises(ConfigError, match="profile not found") as excinfo:
        SettingsFileRepository().unset_value("untaped.skills.updates", profile="ghost")
    assert "ghost" in str(excinfo.value)
    assert "untaped-profile" not in str(excinfo.value)
    assert _isolate_settings.read_text() == original


@pytest.mark.parametrize(
    ("initial", "profile"),
    [
        ("profiles:\n  default:\n    mandatory: x\n", "default"),
        ("profiles:\n  default: {}\n  stage:\n    mandatory: x\nactive: stage\n", "stage"),
    ],
)
def test_unset_leaving_an_invalid_profile_fails_naming_key_and_profile(
    _isolate_settings: Path, initial: str, profile: str
) -> None:
    """Regression (#136): unsetting a required-without-default setting fails
    here, naming the key and the profile, instead of breaking the next
    ``get_settings`` with an opaque pydantic error; the file is untouched."""
    _isolate_settings.write_text(initial)
    repo = SettingsFileRepository(settings_cls=cast(type[Settings], StrictSettings))
    with pytest.raises(ConfigError) as exc_info:
        repo.unset_value("mandatory")
    assert "mandatory" in str(exc_info.value)
    assert profile in str(exc_info.value)
    assert _isolate_settings.read_text() == initial
