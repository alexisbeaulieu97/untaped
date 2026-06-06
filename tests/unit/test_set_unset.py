from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, SecretStr

from untaped import ConfigError
from untaped.config.application import SetSetting, UnsetSetting
from untaped.config.infrastructure import SettingsFileRepository
from untaped.settings import (
    get_settings,
    register_profile_settings,
    reset_config_registry_for_tests,
)


class DemoPluginSettings(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None
    page_size: int = 200


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    reset_config_registry_for_tests()
    register_profile_settings("demo", DemoPluginSettings)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    get_settings.cache_clear()


# ── set ──────────────────────────────────────────────────────────────────────


def test_set_writes_into_default_profile_when_no_active(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("log_level", "DEBUG")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"profiles": {"default": {"log_level": "DEBUG"}}}


def test_set_writes_into_active_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\nactive: prod\n")
    SetSetting(SettingsFileRepository())("log_level", "DEBUG")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["prod"] == {"log_level": "DEBUG"}
    assert data["profiles"]["default"] == {}
    assert data["active"] == "prod"


def test_set_writes_into_named_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")
    SetSetting(SettingsFileRepository())("demo.token", "ghp_xxx", profile="prod")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["prod"] == {"demo": {"token": "ghp_xxx"}}
    assert data["profiles"]["default"] == {}


def test_set_creates_nested_path(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("demo.token", "ghp_xxx")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"profiles": {"default": {"demo": {"token": "ghp_xxx"}}}}


def test_set_coerces_yaml_scalars(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("http.verify_ssl", "false")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"profiles": {"default": {"http": {"verify_ssl": False}}}}


def test_set_validates_via_pydantic(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="invalid value"):
        SetSetting(SettingsFileRepository())("http.verify_ssl", "not-a-bool-or-anything")


def test_set_validation_isolated_from_env_overlay(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation must judge the merged YAML dict alone — not what the
    final loaded ``Settings`` *would* look like with current env vars
    overlaid. Otherwise an env-var sets a valid runtime value and
    masks an invalid value landing on disk; the day the env var goes
    away, ``get_settings()`` falls over with no clue who wrote it.

    Pins the issue #136 fix: ``untaped.validate_settings_isolated``
    builds a one-shot Settings subclass that uses only ``init_settings``
    so the source chain doesn't paper over a structurally bad write."""
    # Env says verify_ssl=true (valid). If validation consulted env,
    # the bad YAML write below would be accepted because env wins.
    monkeypatch.setenv("UNTAPED_HTTP__VERIFY_SSL", "true")
    with pytest.raises(ConfigError, match="invalid value"):
        SetSetting(SettingsFileRepository())("http.verify_ssl", "not-a-bool-or-anything")


def test_set_validates_target_profile_not_ambient_active(_isolate_settings: Path) -> None:
    """Validation must merge from the target profile's perspective.

    Otherwise an invalid value lands in a non-active profile and only
    explodes later when that profile becomes active — by which point the
    user is already past the failed `set` and has lost the validation
    feedback.
    """
    _isolate_settings.write_text(
        "profiles:\n  default:\n    demo:\n      page_size: 50\n  stage: {}\nactive: default\n"
    )
    with pytest.raises(ConfigError, match="invalid value"):
        SetSetting(SettingsFileRepository())("demo.page_size", "abc", profile="stage")


def test_set_rejects_unknown_key(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        SetSetting(SettingsFileRepository())("bogus.key", "x")


def test_set_rejects_unknown_profile(_isolate_settings: Path) -> None:
    """Explicit target profile names the profile plugin remediation.

    This stays distinct from the implicit-path message added for issue #22,
    so a future refactor can't accidentally collapse the two phrasings (the
    user typed a wrong flag here, while a stale persisted ``active:`` points
    to ``profile use`` instead).
    """
    _isolate_settings.write_text("profiles:\n  default: {}\n")
    with pytest.raises(ConfigError, match=r"untaped-profile") as excinfo:
        SetSetting(SettingsFileRepository())("log_level", "DEBUG", profile="ghost")
    assert "ghost" in str(excinfo.value)
    assert "profile create" in str(excinfo.value)


def test_set_default_profile_auto_creates_default_block(_isolate_settings: Path) -> None:
    """Writing to `default` is always allowed; bootstraps the default profile."""
    SetSetting(SettingsFileRepository())("log_level", "DEBUG", profile="default")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"profiles": {"default": {"log_level": "DEBUG"}}}


def test_set_preserves_other_profiles_and_state(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n    log_level: DEBUG\n"
        "  prod:\n    demo:\n      base_url: https://prod\n"
        "active: prod\n"
        "workspace:\n  workspaces:\n    - name: ws1\n      path: /tmp/ws1\n"
    )
    SetSetting(SettingsFileRepository())("demo.token", "tok", profile="default")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"]["log_level"] == "DEBUG"
    assert data["profiles"]["default"]["demo"]["token"] == "tok"
    assert data["profiles"]["prod"]["demo"]["base_url"] == "https://prod"
    assert data["active"] == "prod"
    assert data["workspace"]["workspaces"][0]["name"] == "ws1"


def test_set_ui_theme_writes_top_level_ui_without_creating_profile(
    _isolate_settings: Path,
) -> None:
    target = SetSetting(SettingsFileRepository())("ui.theme", "classic")

    data = yaml.safe_load(_isolate_settings.read_text())
    assert target == "global"
    assert data == {"ui": {"theme": "classic"}}


def test_set_ui_collection_view_accepts_valid_literal(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("ui.collection_view", "list")

    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"ui": {"collection_view": "list"}}


def test_set_ui_collection_view_rejects_invalid_literal_atomically(
    _isolate_settings: Path,
) -> None:
    original = "ui:\n  theme: classic\n"
    _isolate_settings.write_text(original)

    with pytest.raises(ConfigError, match="invalid value"):
        SetSetting(SettingsFileRepository())("ui.collection_view", "nope")

    assert _isolate_settings.read_text() == original


def test_set_rejects_target_profile_for_global_ui(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")

    with pytest.raises(ConfigError, match="global"):
        SetSetting(SettingsFileRepository())("ui.theme", "classic", profile="prod")

    assert yaml.safe_load(_isolate_settings.read_text()) == {
        "profiles": {"default": {}, "prod": {}}
    }


def test_set_rejects_non_ui_top_level_state(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        SetSetting(SettingsFileRepository())("plugins.tool.spec", "untaped")

    assert not _isolate_settings.exists()


# ── unset ────────────────────────────────────────────────────────────────────


def test_unset_removes_key_from_active_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\nactive: default\n")
    removed, target = UnsetSetting(SettingsFileRepository())("log_level")
    assert removed is True
    assert target == "default"
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"] == {}


def test_unset_targets_named_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n  default:\n    log_level: INFO\n  prod:\n    log_level: DEBUG\nactive: prod\n"
    )
    removed, target = UnsetSetting(SettingsFileRepository())("log_level", profile="default")
    assert removed is True
    assert target == "default"
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"] == {}
    assert data["profiles"]["prod"]["log_level"] == "DEBUG"


def test_unset_cleans_empty_parent_within_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    demo:\n      token: x\n")
    UnsetSetting(SettingsFileRepository())("demo.token")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"] == {}


def test_unset_keeps_other_keys_in_parent(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n  default:\n    demo:\n      token: x\n      base_url: https://y\n"
    )
    UnsetSetting(SettingsFileRepository())("demo.token")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"]["demo"] == {"base_url": "https://y"}


def test_unset_returns_false_when_not_set(_isolate_settings: Path) -> None:
    removed, _ = UnsetSetting(SettingsFileRepository())("log_level")
    assert removed is False


def test_unset_ui_theme_removes_top_level_key_and_cleans_empty_ui(
    _isolate_settings: Path,
) -> None:
    _isolate_settings.write_text("ui:\n  theme: classic\nprofiles:\n  default: {}\n")

    removed, target = UnsetSetting(SettingsFileRepository())("ui.theme")

    assert removed is True
    assert target == "global"
    assert yaml.safe_load(_isolate_settings.read_text()) == {"profiles": {"default": {}}}


def test_unset_rejects_target_profile_for_global_ui(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("ui:\n  theme: classic\nprofiles:\n  default: {}\n  prod: {}\n")

    with pytest.raises(ConfigError, match="global"):
        UnsetSetting(SettingsFileRepository())("ui.theme", profile="prod")

    assert yaml.safe_load(_isolate_settings.read_text()) == {
        "ui": {"theme": "classic"},
        "profiles": {"default": {}, "prod": {}},
    }


def test_unset_raises_when_named_profile_missing(_isolate_settings: Path) -> None:
    """Explicit-profile ``unset`` must raise like ``set`` does."""
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\n")
    with pytest.raises(ConfigError, match=r"profile.*ghost"):
        UnsetSetting(SettingsFileRepository())("log_level", profile="ghost")


# ── issue #22: validate recorded `active:` on the implicit path ─────────────


def test_unset_raises_when_recorded_active_missing(_isolate_settings: Path) -> None:
    """Issue #22: ``unset`` with ``active: ghost`` (no ``profiles.ghost``)
    used to silently no-op. Now raises with a message naming the missing
    profile, matching the shape ``list`` already had."""
    _isolate_settings.write_text("active: ghost\nprofiles:\n  default:\n    log_level: INFO\n")
    with pytest.raises(ConfigError, match=r"active profile.*ghost"):
        UnsetSetting(SettingsFileRepository())("log_level")


# ── issue #136: post-unset schema validation ─────────────────────────────────


def test_unset_succeeds_when_schema_default_fills_the_gap(_isolate_settings: Path) -> None:
    """Removing a field that has a schema default must still succeed —
    the default takes over, the merged dict stays valid. This is the
    realistic happy path today (every field has a default), so a
    regression here would break every user's ``unset`` call."""
    _isolate_settings.write_text("profiles:\n  default:\n    demo:\n      page_size: 50\n")
    removed, target = UnsetSetting(SettingsFileRepository())("demo.page_size")
    assert removed is True
    assert target == "default"
    data = yaml.safe_load(_isolate_settings.read_text())
    assert "demo" not in data["profiles"]["default"]


def test_unset_raises_when_post_state_is_invalid(_isolate_settings: Path) -> None:
    """If a setting were required-without-default and a user unset it,
    the next ``get_settings`` would fail with an opaque pydantic error
    far from the call site. Validate on unset and raise ``ConfigError``
    naming the key so the failure surfaces here, not later.

    Synthesised via a ``Settings`` subclass that drops the default on
    ``log_level`` — no real setting today is required-without-default,
    but this is preventive plumbing so future ones can't regress."""
    from typing import cast

    from pydantic import Field
    from pydantic_settings import SettingsConfigDict

    from untaped.settings import Settings

    class StrictSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="UNTAPED_",
            env_nested_delimiter="__",
            extra="ignore",
        )
        # Ellipsis ``...`` makes the field required and overrides the
        # base class's ``log_level: str = "INFO"`` default.
        log_level: str = Field(...)  # type: ignore[assignment]

    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\n")
    before = _isolate_settings.read_bytes()
    repo = SettingsFileRepository(settings_cls=cast(type[Settings], StrictSettings))
    with pytest.raises(ConfigError, match=r"log_level"):
        UnsetSetting(repo)("log_level")
    # File must be unchanged — ``mutate_config``'s atomic write only
    # flushes if the callback returns successfully, and our validation
    # error raised inside the callback.
    assert _isolate_settings.read_bytes() == before


def test_unset_error_message_names_the_key_and_profile(_isolate_settings: Path) -> None:
    """The error message must mention both the key the user tried to
    unset and the profile so they can find the offending edit without
    re-reading the YAML. Mirrors ``set_value``'s "invalid value for
    {key!r}" shape — the two messages live on the same code path so
    users see uniform diagnostics."""
    from typing import cast

    from pydantic import Field
    from pydantic_settings import SettingsConfigDict

    from untaped.settings import Settings

    class StrictSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="UNTAPED_",
            env_nested_delimiter="__",
            extra="ignore",
        )
        # Ellipsis makes the field required and overrides the base's default.
        log_level: str = Field(...)  # type: ignore[assignment]

    # Stage has the only ``log_level``; no default-profile fallback. After
    # unset, the merged view for ``active: stage`` is missing the
    # required field, so validation fails and the error names the target
    # profile ("stage"), not "default".
    _isolate_settings.write_text(
        "profiles:\n  default: {}\n  stage:\n    log_level: WARN\nactive: stage\n"
    )
    repo = SettingsFileRepository(settings_cls=cast(type[Settings], StrictSettings))
    with pytest.raises(ConfigError) as exc_info:
        UnsetSetting(repo)("log_level")
    message = str(exc_info.value)
    assert "log_level" in message
    assert "stage" in message


def test_set_raises_with_resolution_time_message_when_recorded_active_missing(
    _isolate_settings: Path,
) -> None:
    """Issue #22: ``set`` raises at resolution time with the actionable
    "Run `untaped profile use ...`" message — distinct from the
    schema-validation pathway that also pre-existed (and which produces
    `_select_active`'s tersere message). Also asserts the file is
    untouched on failure (mutate_config's atomic-write contract holds)."""
    _isolate_settings.write_text("active: ghost\nprofiles:\n  default:\n    log_level: INFO\n")
    before = _isolate_settings.read_bytes()
    with pytest.raises(ConfigError, match=r"profile use") as excinfo:
        SetSetting(SettingsFileRepository())("log_level", "DEBUG")
    assert "ghost" in str(excinfo.value)
    assert _isolate_settings.read_bytes() == before


def test_set_raises_when_env_active_missing(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``UNTAPED_PROFILE`` pointing at a missing profile also raises on
    the implicit path (no ``--profile`` flag)."""
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: INFO\n")
    monkeypatch.setenv("UNTAPED_PROFILE", "ghost")
    with pytest.raises(ConfigError, match=r"active profile.*ghost"):
        SetSetting(SettingsFileRepository())("log_level", "DEBUG")
