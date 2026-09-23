from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr, ValidationError, field_validator

from untaped.errors import ConfigError
from untaped.settings import (
    Settings,
    get_settings,
    load_settings_section,
    register_profile_settings,
    reset_config_registry_for_tests,
    resolve_config_path,
    validate_settings_section,
)


class DemoPluginSettings(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None
    api_prefix: str = "/api/demo/v1/"
    default_organization: str | None = None
    page_size: int = 200

    @field_validator("api_prefix")
    @classmethod
    def _api_prefix_has_slashes(cls, value: str) -> str:
        if not (value.startswith("/") and value.endswith("/")):
            raise ValueError("must start and end with /")
        return value


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    reset_config_registry_for_tests()
    register_profile_settings("demo", DemoPluginSettings)
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def test_defaults_when_no_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    s = get_settings()
    assert s.log_level == "INFO"
    assert s.demo.base_url is None
    assert s.demo.token is None
    assert s.http.verify_ssl is True
    assert s.http.ca_bundle is None


def test_loads_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    # ``log_level``, ``http`` and tool sections (``demo``) are all profile-scoped
    # now, so they live under ``profiles.default``.
    cfg.write_text(
        """
        profiles:
          default:
            log_level: DEBUG
            http:
              ca_bundle: /etc/ssl/corp-ca.pem
              verify_ssl: true
            demo:
              base_url: https://aap.example.com
              token: secret
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.log_level == "DEBUG"
    assert s.http.ca_bundle == Path("/etc/ssl/corp-ca.pem")
    assert s.demo.base_url == "https://aap.example.com"
    assert s.demo.token is not None
    assert s.demo.token.get_secret_value() == "secret"


def test_secret_str_repr_does_not_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    demo:\n      token: ultra-secret-value\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.demo.token is not None
    assert s.demo.token.get_secret_value() == "ultra-secret-value"
    assert "ultra-secret-value" not in repr(s)
    assert "ultra-secret-value" not in str(s)
    assert "ultra-secret-value" not in str(s.demo)


def test_env_var_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("demo:\n  token: from-yaml\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "from-env")
    s = get_settings()
    assert s.demo.token is not None
    assert s.demo.token.get_secret_value() == "from-env"


def test_get_settings_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    a = get_settings()
    b = get_settings()
    assert a is b


def test_settings_class_can_be_instantiated_directly() -> None:
    s = Settings()
    assert isinstance(s, Settings)


def test_plugin_defaults_are_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    s = get_settings()
    assert s.demo.api_prefix == "/api/demo/v1/"
    assert s.demo.default_organization is None
    assert s.demo.page_size == 200


def test_ui_settings_default_to_global_default_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    s = get_settings()
    assert s.ui.theme == "default"
    assert s.ui.border is None
    assert s.ui.collection_view is None


def test_ui_settings_load_from_profile_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    # ``ui`` (like ``log_level``) is profile-scoped now and lives under
    # ``profiles.default``.
    cfg.write_text(
        """
        profiles:
          default:
            log_level: DEBUG
            ui:
              theme: compact
              border: square
              collection_view: list
              symbols:
                warning: "!"
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.log_level == "DEBUG"
    assert s.ui.theme == "compact"
    assert s.ui.border == "square"
    assert s.ui.collection_view == "list"
    assert s.ui.symbols == {"warning": "!"}


def test_plugin_loads_extended_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            demo:
              base_url: https://awx.example.com
              api_prefix: /api/v2/
              default_organization: Default
              page_size: 100
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.demo.api_prefix == "/api/v2/"
    assert s.demo.default_organization == "Default"
    assert s.demo.page_size == 100


def test_plugin_api_prefix_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    monkeypatch.setenv("UNTAPED_DEMO__API_PREFIX", "/api/v2/")
    s = get_settings()
    assert s.demo.api_prefix == "/api/v2/"


def test_plugin_api_prefix_must_start_and_end_with_slash() -> None:
    with pytest.raises(ValidationError):
        DemoPluginSettings(api_prefix="api/v2/")
    with pytest.raises(ValidationError):
        DemoPluginSettings(api_prefix="/api/v2")


def test_resolve_config_path_honours_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", "~/custom-config.yml")
    assert resolve_config_path() == Path("~/custom-config.yml").expanduser()


def test_resolve_config_path_defaults_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNTAPED_CONFIG", raising=False)
    assert resolve_config_path() == Path("~/.untaped/config.yml").expanduser()


# -------------------- config-load error translation -------------------- #


def test_get_settings_translates_yaml_error_to_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken ``~/.untaped/config.yml`` must surface from ``get_settings``
    as ``ConfigError`` (clean ``error: …``) — not as a ``yaml.YAMLError``
    bubbling out of ``LayoutSettingsSource._load_raw_yaml``."""
    cfg = tmp_path / "config.yml"
    cfg.write_text("log_level: [unterminated\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    with pytest.raises(ConfigError, match=str(cfg)):
        get_settings()


def test_get_settings_translates_validation_error_to_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schema mismatch (e.g. ``page_size: not-an-int``) must surface from
    ``get_settings`` as ``ConfigError`` carrying the offending field — not
    as a multi-line ``pydantic.ValidationError`` traceback."""
    cfg = tmp_path / "config.yml"
    # ``demo`` is profile-scoped: it must live under ``profiles.default`` to
    # be effective and therefore actually validated against the schema.
    cfg.write_text(
        """
        profiles:
          default:
            demo:
              page_size: not-an-int
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    # Match the dotted ``loc`` so a regression that stops joining the path
    # tuple in ``first_validation_error`` would fail the assertion.
    with pytest.raises(ConfigError, match=r"demo\.page_size") as exc_info:
        get_settings()
    # The path of the broken config is in the message so users know where
    # to edit.
    assert str(cfg) in str(exc_info.value)


# -------------------- per-section validation / loading -------------------- #


def test_validate_settings_section_ignores_env_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Validation judges the dict alone: an env var must not paper over a bad
    # value on its way to disk.
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    monkeypatch.setenv("UNTAPED_HTTP__VERIFY_SSL", "true")
    assert Settings().http.verify_ssl is True
    with pytest.raises(ValidationError):
        validate_settings_section({"http": {"verify_ssl": "not-a-bool"}}, "http")


def test_validate_settings_section_ignores_other_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    data = {"log_level": "DEBUG", "demo": {"page_size": "lots"}}
    assert validate_settings_section(data, "log_level") == "DEBUG"
    with pytest.raises(ValidationError):
        validate_settings_section(data, "demo")


def test_load_settings_section_isolated_from_invalid_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    demo:\n      page_size: lots\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    assert load_settings_section("http").timeout == 30.0
    with pytest.raises(ConfigError, match=r"demo\.page_size"):
        load_settings_section("demo")


def test_load_settings_section_names_the_env_var_culprit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    monkeypatch.setenv("UNTAPED_HTTP__TIMEOUT", "abc")
    with pytest.raises(ConfigError, match="environment variable UNTAPED_HTTP__TIMEOUT"):
        load_settings_section("http")
