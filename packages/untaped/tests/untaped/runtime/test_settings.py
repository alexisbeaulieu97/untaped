"""Settings defaults, profile loading, environment precedence, and validation."""

from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from untaped.runtime.settings import (
    get_settings,
    load_core_part,
    load_settings_section,
    register_profile_settings,
    resolve_config_path,
    validate_settings_section,
)
from untaped.sdk.errors import ConfigError


class DemoPluginSettings(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None
    api_prefix: str = "/api/demo/v1/"
    default_organization: str | None = None
    page_size: int = 200


@pytest.fixture(autouse=True)
def _register_demo() -> None:
    # The testing plugin resets the registry and settings cache around each test.
    register_profile_settings("demo", DemoPluginSettings)


def test_defaults_when_no_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    s = get_settings()
    assert s.untaped.skills.updates == "auto"
    assert s.demo.base_url is None
    assert s.demo.token is None
    assert s.untaped.http.verify_ssl is True
    assert s.untaped.http.ca_bundle_path is None


def test_loads_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    # ``skills``, ``http`` and tool sections (``demo``) are all profile-scoped
    # now, so they live under ``profiles.default``.
    cfg.write_text(
        """
        profiles:
          default:
            untaped:
              skills:
                updates: auto
              http:
                ca_bundle_path: /etc/ssl/corp-ca.pem
                verify_ssl: true
            demo:
              base_url: https://aap.example.com
              token: secret
        """
    )
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    s = get_settings()
    assert s.untaped.skills.updates == "auto"
    assert s.untaped.http.ca_bundle_path == Path("/etc/ssl/corp-ca.pem")
    assert s.demo.base_url == "https://aap.example.com"
    assert s.demo.token is not None
    assert s.demo.token.get_secret_value() == "secret"


@pytest.mark.parametrize("profile", ["default", "work"])
def test_env_var_overrides_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        f"active: {profile}\nprofiles:\n  {profile}:\n    demo:\n      token: from-yaml\n"
    )
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    baseline = get_settings()
    assert baseline.demo.token is not None
    assert baseline.demo.token.get_secret_value() == "from-yaml"
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "from-env")
    get_settings.cache_clear()
    s = get_settings()
    assert s.demo.token is not None
    assert s.demo.token.get_secret_value() == "from-env"


def test_plugin_defaults_are_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    s = get_settings()
    assert s.demo.api_prefix == "/api/demo/v1/"
    assert s.demo.default_organization is None
    assert s.demo.page_size == 200


def test_ui_settings_default_to_global_default_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    s = get_settings()
    assert s.untaped.ui.theme == "default"
    assert s.untaped.ui.border is None
    assert s.untaped.ui.collection_view is None


def test_ui_settings_load_from_profile_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    # ``ui`` (like ``skills``) is profile-scoped now and lives under
    # ``profiles.default``.
    cfg.write_text(
        """
        profiles:
          default:
            untaped:
              skills:
                updates: auto
              ui:
                theme: compact
                border: square
                collection_view: list
                symbols:
                  warning: "!"
        """
    )
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    s = get_settings()
    assert s.untaped.skills.updates == "auto"
    assert s.untaped.ui.theme == "compact"
    assert s.untaped.ui.border == "square"
    assert s.untaped.ui.collection_view == "list"
    assert s.untaped.ui.symbols == {"warning": "!"}


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
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    s = get_settings()
    assert s.demo.api_prefix == "/api/v2/"
    assert s.demo.default_organization == "Default"
    assert s.demo.page_size == 100


def test_plugin_api_prefix_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    monkeypatch.setenv("UNTAPED_DEMO__API_PREFIX", "/api/v2/")
    s = get_settings()
    assert s.demo.api_prefix == "/api/v2/"


def test_resolve_config_path_follows_the_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HOME", "~/custom-home")
    assert resolve_config_path() == Path("~/custom-home/config.yml").expanduser()


def test_resolve_config_path_defaults_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNTAPED_HOME", raising=False)
    assert resolve_config_path() == Path("~/.untaped/config.yml").expanduser()


# -------------------- config-load error translation -------------------- #


def test_get_settings_translates_yaml_error_to_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken ``~/.untaped/config.yml`` must surface from ``get_settings``
    as ``ConfigError`` (clean ``error: …``) — not as a ``yaml.YAMLError``
    bubbling out of ``LayoutSettingsSource._load_raw_yaml``."""
    cfg = tmp_path / "config.yml"
    cfg.write_text("untaped:\n  skills: [unterminated\n")
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
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
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
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
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    monkeypatch.setenv("UNTAPED_UNTAPED__HTTP__VERIFY_SSL", "true")
    assert load_core_part("http").verify_ssl is True
    with pytest.raises(ValidationError):
        validate_settings_section({"untaped": {"http": {"verify_ssl": "not-a-bool"}}}, "untaped")


def test_validate_settings_section_ignores_other_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    data = {"demo": {"page_size": "lots"}, "untaped": {"skills": {"updates": "auto"}}}
    assert validate_settings_section(data, "untaped").skills.updates == "auto"
    with pytest.raises(ValidationError):
        validate_settings_section(data, "demo")


def test_load_settings_section_isolated_from_invalid_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    demo:\n      page_size: lots\n")
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    assert load_core_part("http").timeout_seconds == 30.0
    with pytest.raises(ConfigError, match=r"demo\.page_size"):
        load_settings_section("demo")


@pytest.mark.parametrize(
    "variable", [None, ("UNTAPED_UNTAPED__UI__BORDER", "bogus")], ids=["file", "variable"]
)
def test_each_part_of_core_s_section_is_read_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: tuple[str, str] | None
) -> None:
    cfg = tmp_path / "config.yml"
    border = "" if variable else "        border: bogus\n"
    cfg.write_text(
        "profiles:\n  default:\n    untaped:\n"
        f"      http:\n        timeout_seconds: 9\n      ui:\n{border}        theme: plain\n"
    )
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    if variable:
        monkeypatch.setenv(*variable)
    assert load_core_part("http").timeout_seconds == 9.0
    assert load_core_part("skills").updates == "auto"
    with pytest.raises(ConfigError, match="border"):
        load_core_part("ui")


def test_load_settings_section_names_the_env_var_culprit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    monkeypatch.setenv("UNTAPED_UNTAPED__HTTP__TIMEOUT_SECONDS", "abc")
    with pytest.raises(
        ConfigError, match="environment variable UNTAPED_UNTAPED__HTTP__TIMEOUT_SECONDS"
    ):
        load_core_part("http")
