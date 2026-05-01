from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped_core.settings import Settings, get_settings


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
    assert s.github.token is None
    assert s.workspace.workspaces == []
    assert s.http.verify_ssl is True
    assert s.http.ca_bundle is None


def test_loads_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        log_level: DEBUG
        http:
          ca_bundle: /etc/ssl/corp-ca.pem
          verify_ssl: true
        awx:
          base_url: https://aap.example.com
          token: secret
        github:
          token: ghp_xxx
        workspace:
          cache_dir: /custom/cache
          workspaces:
            - name: prod
              path: /tmp/prod
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.log_level == "DEBUG"
    assert s.http.ca_bundle == Path("/etc/ssl/corp-ca.pem")
    assert s.awx.base_url == "https://aap.example.com"
    assert s.awx.token is not None
    assert s.awx.token.get_secret_value() == "secret"
    assert s.github.token is not None
    assert s.github.token.get_secret_value() == "ghp_xxx"
    assert s.workspace.cache_dir == Path("/custom/cache")
    assert len(s.workspace.workspaces) == 1
    assert s.workspace.workspaces[0].name == "prod"
    assert s.workspace.workspaces[0].path == "/tmp/prod"


def test_workspace_cache_dir_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    s = get_settings()
    assert s.workspace.cache_dir == Path("~/.untaped/repositories")


def test_secret_str_repr_does_not_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("awx:\n  token: ultra-secret-value\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert "ultra-secret-value" not in repr(s)
    assert "ultra-secret-value" not in str(s)
    assert "ultra-secret-value" not in str(s.awx)


def test_env_var_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("awx:\n  token: from-yaml\n")
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
