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


# ── set (flat layout) ────────────────────────────────────────────────────────


def test_set_writes_top_level_key(_isolate_settings: Path) -> None:
    target = SetSetting(SettingsFileRepository())("log_level", "DEBUG")
    assert target is None
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"log_level": "DEBUG"}


def test_set_creates_nested_path(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("demo.token", "ghp_xxx")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"demo": {"token": "ghp_xxx"}}


def test_set_coerces_yaml_scalars(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("http.verify_ssl", "false")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"http": {"verify_ssl": False}}


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


def test_set_rejects_unknown_key(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        SetSetting(SettingsFileRepository())("bogus.key", "x")


def test_set_target_profile_errors_without_profile_plugin(_isolate_settings: Path) -> None:
    """Flat layout has no scopes; an explicit target names the remediation."""
    with pytest.raises(ConfigError, match="profiles are not available") as excinfo:
        SetSetting(SettingsFileRepository())("log_level", "DEBUG", profile="prod")
    assert "prod" in str(excinfo.value)
    assert "untaped-profile" in str(excinfo.value)
    assert not _isolate_settings.exists()


def test_set_ignores_profile_shaped_yaml_and_writes_top_level(_isolate_settings: Path) -> None:
    """A leftover ``profiles:`` block (plugin uninstalled) is inert: writes
    land at the top level and the block survives untouched."""
    _isolate_settings.write_text("profiles:\n  prod:\n    log_level: WARNING\n")
    SetSetting(SettingsFileRepository())("log_level", "DEBUG")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["log_level"] == "DEBUG"
    assert data["profiles"] == {"prod": {"log_level": "WARNING"}}


def test_set_preserves_other_keys_and_state(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "log_level: DEBUG\n"
        "demo:\n  base_url: https://prod\n"
        "workspace:\n  workspaces:\n    - name: ws1\n      path: /tmp/ws1\n"
    )
    SetSetting(SettingsFileRepository())("demo.token", "tok")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["log_level"] == "DEBUG"
    assert data["demo"]["token"] == "tok"
    assert data["demo"]["base_url"] == "https://prod"
    assert data["workspace"]["workspaces"][0]["name"] == "ws1"


def test_set_ui_theme_writes_top_level_ui(_isolate_settings: Path) -> None:
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
    original = "log_level: INFO\n"
    _isolate_settings.write_text(original)

    with pytest.raises(ConfigError, match="global"):
        SetSetting(SettingsFileRepository())("ui.theme", "classic", profile="prod")

    assert _isolate_settings.read_text() == original


def test_set_rejects_non_ui_top_level_state(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        SetSetting(SettingsFileRepository())("plugins.tool.spec", "untaped")

    assert not _isolate_settings.exists()


# ── unset (flat layout) ──────────────────────────────────────────────────────


def test_unset_removes_top_level_key(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("log_level: DEBUG\ndemo:\n  base_url: https://x\n")
    removed, target = UnsetSetting(SettingsFileRepository())("log_level")
    assert removed is True
    assert target is None
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"demo": {"base_url": "https://x"}}


def test_unset_cleans_empty_parent(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("log_level: DEBUG\ndemo:\n  token: x\n")
    UnsetSetting(SettingsFileRepository())("demo.token")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"log_level": "DEBUG"}


def test_unset_keeps_other_keys_in_parent(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("demo:\n  token: x\n  base_url: https://y\n")
    UnsetSetting(SettingsFileRepository())("demo.token")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["demo"] == {"base_url": "https://y"}


def test_unset_returns_false_when_not_set(_isolate_settings: Path) -> None:
    removed, target = UnsetSetting(SettingsFileRepository())("log_level")
    assert removed is False
    assert target is None


def test_unset_target_profile_errors_without_profile_plugin(_isolate_settings: Path) -> None:
    """Explicit-profile ``unset`` must raise like ``set`` does in flat mode."""
    original = "log_level: DEBUG\n"
    _isolate_settings.write_text(original)
    with pytest.raises(ConfigError, match="profiles are not available") as excinfo:
        UnsetSetting(SettingsFileRepository())("log_level", profile="ghost")
    assert "ghost" in str(excinfo.value)
    assert _isolate_settings.read_text() == original


def test_unset_ui_theme_removes_top_level_key_and_cleans_empty_ui(
    _isolate_settings: Path,
) -> None:
    _isolate_settings.write_text("ui:\n  theme: classic\nlog_level: DEBUG\n")

    removed, target = UnsetSetting(SettingsFileRepository())("ui.theme")

    assert removed is True
    assert target == "global"
    assert yaml.safe_load(_isolate_settings.read_text()) == {"log_level": "DEBUG"}


def test_unset_rejects_target_profile_for_global_ui(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("ui:\n  theme: classic\n")

    with pytest.raises(ConfigError, match="global"):
        UnsetSetting(SettingsFileRepository())("ui.theme", profile="prod")

    assert yaml.safe_load(_isolate_settings.read_text()) == {"ui": {"theme": "classic"}}


# ── issue #136: post-unset schema validation ─────────────────────────────────


def test_unset_succeeds_when_schema_default_fills_the_gap(_isolate_settings: Path) -> None:
    """Removing a field that has a schema default must still succeed —
    the default takes over, the merged dict stays valid. This is the
    realistic happy path today (every field has a default), so a
    regression here would break every user's ``unset`` call."""
    _isolate_settings.write_text("demo:\n  page_size: 50\n")
    removed, target = UnsetSetting(SettingsFileRepository())("demo.page_size")
    assert removed is True
    assert target is None
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {}


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

    _isolate_settings.write_text("log_level: DEBUG\n")
    before = _isolate_settings.read_bytes()
    repo = SettingsFileRepository(settings_cls=cast(type[Settings], StrictSettings))
    with pytest.raises(ConfigError, match=r"log_level"):
        UnsetSetting(repo)("log_level")
    # File must be unchanged — ``mutate_config``'s atomic write only
    # flushes if the callback returns successfully, and our validation
    # error raised inside the callback.
    assert _isolate_settings.read_bytes() == before


def test_unset_error_message_names_the_key_and_the_config(_isolate_settings: Path) -> None:
    """The error message must mention both the key the user tried to
    unset and where the removal landed (``the config`` in flat mode) so
    they can find the offending edit without re-reading the YAML.
    Mirrors ``set_value``'s "invalid value for {key!r}" shape — the two
    messages live on the same code path so users see uniform
    diagnostics."""
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

    _isolate_settings.write_text("log_level: WARN\n")
    repo = SettingsFileRepository(settings_cls=cast(type[Settings], StrictSettings))
    with pytest.raises(ConfigError) as exc_info:
        UnsetSetting(repo)("log_level")
    message = str(exc_info.value)
    assert "log_level" in message
    assert "the config" in message


# ── scoped layout pass-through (FakeScopedLayout) ────────────────────────────


class TestScopedLayoutWrites:
    """Core's write plumbing against a registered scoped layout.

    The real scoped layout (and its policy details) live in the
    untaped-profile plugin; these tests only pin that core routes writes
    through ``write_scope`` and reports the resolved scope name."""

    @pytest.fixture(autouse=True)
    def _scoped(self, _isolate_settings: Path, fake_scoped_layout: object) -> Iterator[None]:
        yield

    def test_set_writes_into_active_scope_and_returns_its_name(
        self, _isolate_settings: Path
    ) -> None:
        _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\nactive: prod\n")
        target = SetSetting(SettingsFileRepository())("log_level", "DEBUG")
        assert target == "prod"
        data = yaml.safe_load(_isolate_settings.read_text())
        assert data["profiles"]["prod"] == {"log_level": "DEBUG"}
        assert data["profiles"]["default"] == {}

    def test_set_writes_into_named_scope(self, _isolate_settings: Path) -> None:
        _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")
        target = SetSetting(SettingsFileRepository())("demo.token", "ghp_xxx", profile="prod")
        assert target == "prod"
        data = yaml.safe_load(_isolate_settings.read_text())
        assert data["profiles"]["prod"] == {"demo": {"token": "ghp_xxx"}}
        assert data["profiles"]["default"] == {}

    def test_set_rejects_unknown_scope(self, _isolate_settings: Path) -> None:
        _isolate_settings.write_text("profiles:\n  default: {}\n")
        with pytest.raises(ConfigError, match="ghost"):
            SetSetting(SettingsFileRepository())("log_level", "DEBUG", profile="ghost")

    def test_set_validates_target_scope_not_ambient_active(self, _isolate_settings: Path) -> None:
        """Validation must merge from the target scope's perspective.

        Otherwise an invalid value lands in a non-active scope and only
        explodes later when that scope becomes active — by which point
        the user is already past the failed ``set`` and has lost the
        validation feedback."""
        _isolate_settings.write_text(
            "profiles:\n  default:\n    demo:\n      page_size: 50\n  stage: {}\nactive: default\n"
        )
        with pytest.raises(ConfigError, match="invalid value"):
            SetSetting(SettingsFileRepository())("demo.page_size", "abc", profile="stage")

    def test_unset_removes_key_from_named_scope(self, _isolate_settings: Path) -> None:
        _isolate_settings.write_text(
            "profiles:\n"
            "  default:\n    log_level: INFO\n"
            "  prod:\n    log_level: DEBUG\n"
            "active: prod\n"
        )
        removed, target = UnsetSetting(SettingsFileRepository())("log_level", profile="default")
        assert removed is True
        assert target == "default"
        data = yaml.safe_load(_isolate_settings.read_text())
        assert data["profiles"]["default"] == {}
        assert data["profiles"]["prod"]["log_level"] == "DEBUG"

    def test_unset_error_names_the_scope(self, _isolate_settings: Path) -> None:
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
            log_level: str = Field(...)  # type: ignore[assignment]

        _isolate_settings.write_text(
            "profiles:\n  default: {}\n  stage:\n    log_level: WARN\nactive: stage\n"
        )
        repo = SettingsFileRepository(settings_cls=cast(type[Settings], StrictSettings))
        with pytest.raises(ConfigError) as exc_info:
            UnsetSetting(repo)("log_level")
        message = str(exc_info.value)
        assert "log_level" in message
        assert "stage" in message
