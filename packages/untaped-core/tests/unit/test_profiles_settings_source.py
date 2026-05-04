"""End-to-end tests for the profile-aware ``Settings`` loader.

These tests exercise ``Settings`` against the new YAML schema (profiles +
top-level workspace registry). They cover the merge behaviour, the
``UNTAPED_PROFILE`` env-override, and the registry-hoisting trick that
keeps ``Settings.workspace.workspaces`` working when the registry lives
outside any profile.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped_core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_loads_default_profile_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            log_level: DEBUG
            awx:
              base_url: https://aap.local
              token: secret-default
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.log_level == "DEBUG"
    assert s.awx.base_url == "https://aap.local"
    assert s.awx.token is not None
    assert s.awx.token.get_secret_value() == "secret-default"


def test_active_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            log_level: INFO
            awx:
              base_url: https://default.example
              token: default-tok
              api_prefix: /api/v2/
          prod:
            log_level: WARNING
            awx:
              base_url: https://prod.example
              token: prod-tok
        active: prod
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.log_level == "WARNING"
    assert s.awx.base_url == "https://prod.example"
    assert s.awx.token is not None
    assert s.awx.token.get_secret_value() == "prod-tok"
    # api_prefix stays from default (not redeclared in prod)
    assert s.awx.api_prefix == "/api/v2/"


def test_untaped_profile_env_overrides_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://default.example
          prod:
            awx:
              base_url: https://prod.example
          stage:
            awx:
              base_url: https://stage.example
        active: prod
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.setenv("UNTAPED_PROFILE", "stage")
    s = get_settings()
    assert s.awx.base_url == "https://stage.example"


def test_untaped_field_env_still_beats_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              token: from-yaml
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.setenv("UNTAPED_AWX__TOKEN", "from-env")
    s = get_settings()
    assert s.awx.token is not None
    assert s.awx.token.get_secret_value() == "from-env"


def test_workspace_registry_lives_at_top_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default: {}
        workspace:
          workspaces:
            - name: prod
              path: /tmp/prod
            - name: stage
              path: /tmp/stage
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    names = [w.name for w in s.workspace.workspaces]
    assert names == ["prod", "stage"]


def test_workspace_cache_dir_can_live_in_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            workspace:
              cache_dir: /default/cache
          prod:
            workspace:
              cache_dir: /prod/cache
        active: prod
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.workspace.cache_dir == Path("/prod/cache")


def test_top_level_workspace_registry_does_not_clobber_profile_cache_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry hoist must merge with (not overwrite) the workspace block."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            workspace:
              cache_dir: /from/profile
        workspace:
          workspaces:
            - name: prod
              path: /tmp/prod
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.workspace.cache_dir == Path("/from/profile")
    assert len(s.workspace.workspaces) == 1
    assert s.workspace.workspaces[0].name == "prod"


def test_empty_config_file_yields_schema_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    s = get_settings()
    assert s.log_level == "INFO"
    assert s.awx.token is None
    assert s.workspace.workspaces == []


def test_missing_default_profile_no_active_uses_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`default` is optional. With profiles defined but no `default` and
    no `active:` key, no profile layer applies — Settings falls through
    to schema defaults (`log_level == "INFO"`, `awx.token is None`)."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          prod: {awx: {token: x}}
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = Settings()
    assert s.log_level == "INFO"
    assert s.awx.token is None


def test_missing_default_profile_with_active_uses_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `active: prod` and no `default`, prod's values still apply
    — schema defaults fill the rest."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          prod: {awx: {token: x}}
        active: prod
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = Settings()
    assert s.log_level == "INFO"
    assert s.awx.token is not None
    assert s.awx.token.get_secret_value() == "x"


def test_unknown_active_profile_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default: {}
        active: ghost
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    with pytest.raises(Exception) as exc:
        Settings()
    assert "ghost" in str(exc.value)
