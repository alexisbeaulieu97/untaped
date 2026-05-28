from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from untaped import ConfigError, resolve_config_path, validate_settings_isolated
from untaped.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_when_no_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    s = get_settings()
    assert s.log_level == "INFO"
    assert s.awx.base_url is None
    assert s.awx.token is None
    assert s.http.verify_ssl is True
    assert s.http.ca_bundle is None


def test_loads_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            log_level: DEBUG
            http:
              ca_bundle: /etc/ssl/corp-ca.pem
              verify_ssl: true
            awx:
              base_url: https://aap.example.com
              token: secret
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.log_level == "DEBUG"
    assert s.http.ca_bundle == Path("/etc/ssl/corp-ca.pem")
    assert s.awx.base_url == "https://aap.example.com"
    assert s.awx.token is not None
    assert s.awx.token.get_secret_value() == "secret"


def test_secret_str_repr_does_not_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    awx:\n      token: ultra-secret-value\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.awx.token is not None
    assert s.awx.token.get_secret_value() == "ultra-secret-value"
    assert "ultra-secret-value" not in repr(s)
    assert "ultra-secret-value" not in str(s)
    assert "ultra-secret-value" not in str(s.awx)


def test_env_var_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    awx:\n      token: from-yaml\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.setenv("UNTAPED_AWX__TOKEN", "from-env")
    s = get_settings()
    assert s.awx.token is not None
    assert s.awx.token.get_secret_value() == "from-env"


def test_get_settings_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    a = get_settings()
    b = get_settings()
    assert a is b


def test_settings_class_can_be_instantiated_directly() -> None:
    s = Settings()
    assert isinstance(s, Settings)


def test_awx_defaults_aap_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AAP is the default deployment target; controller v2 prefix is canonical."""
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    s = get_settings()
    assert s.awx.api_prefix == "/api/controller/v2/"
    assert s.awx.default_organization is None
    assert s.awx.page_size == 200


def test_awx_loads_extended_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://awx.example.com
              api_prefix: /api/v2/
              default_organization: Default
              page_size: 100
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.awx.api_prefix == "/api/v2/"
    assert s.awx.default_organization == "Default"
    assert s.awx.page_size == 100


def test_awx_api_prefix_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    monkeypatch.setenv("UNTAPED_AWX__API_PREFIX", "/api/v2/")
    s = get_settings()
    assert s.awx.api_prefix == "/api/v2/"


def test_awx_api_prefix_must_start_and_end_with_slash() -> None:
    from pydantic import ValidationError
    from untaped_awx.infrastructure import AwxConfig

    with pytest.raises(ValidationError):
        AwxConfig(api_prefix="api/v2/")
    with pytest.raises(ValidationError):
        AwxConfig(api_prefix="/api/v2")


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
    bubbling out of ``ProfilesSettingsSource._load_raw_yaml``."""
    cfg = tmp_path / "config.yml"
    cfg.write_text("active: [unterminated\n")
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
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              page_size: not-an-int
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    # Match the dotted ``loc`` so a regression that stops joining the path
    # tuple in ``first_validation_error`` would fail the assertion.
    with pytest.raises(ConfigError, match=r"awx\.page_size") as exc_info:
        get_settings()
    # The path of the broken config is in the message so users know where
    # to edit.
    assert str(cfg) in str(exc_info.value)


# -------------------- validate_settings_isolated -------------------- #


def test_validate_settings_isolated_ignores_env_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If validation re-ran the source chain, the env var would land on
    # top of the dict and the bad value would silently validate against
    # the env-supplied value. The helper exists precisely to bypass that.
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    monkeypatch.setenv("UNTAPED_HTTP__VERIFY_SSL", "true")
    # Positive control: vanilla Settings() honours the env var, proving
    # the env precedence chain actually fires. Without this, the next
    # assertion would still pass on a typo'd env name.
    assert Settings().http.verify_ssl is True
    with pytest.raises(ValidationError):
        validate_settings_isolated({"http": {"verify_ssl": "not-a-bool"}})


def test_validate_settings_isolated_returns_validated_settings_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    result = validate_settings_isolated({"log_level": "DEBUG", "awx": {"page_size": 50}})
    assert isinstance(result, Settings)
    assert result.log_level == "DEBUG"
    assert result.awx.page_size == 50


def test_validate_settings_isolated_does_not_mutate_settings_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin method identity directly: the helper must build a one-shot
    # *subclass* to override settings_customise_sources, never mutate
    # the base class. A regression that swapped the override onto
    # ``Settings`` itself would silently break ``get_settings()`` for
    # every later caller in the process.
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    # The ``@classmethod`` descriptor binds a fresh bound-method on every
    # attribute access, so compare ``.__func__`` (the underlying function)
    # to pin the no-mutation invariant.
    before = Settings.settings_customise_sources.__func__
    validate_settings_isolated({"log_level": "ERROR"})
    assert Settings.settings_customise_sources.__func__ is before
