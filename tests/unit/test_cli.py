from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, SecretStr
from typer.testing import CliRunner

from untaped.config import app
from untaped.settings import (
    get_settings,
    register_profile_settings,
    reset_config_registry_for_tests,
)


class DemoPluginSettings(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None


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


def test_list_outputs_keys(_isolate_settings: Path) -> None:
    result = CliRunner().invoke(app, ["list", "--format", "raw", "--columns", "key"])
    assert result.exit_code == 0, result.output
    keys = result.stdout.splitlines()
    assert "log_level" in keys
    assert "demo.token" in keys
    assert "http.ca_bundle" in keys
    assert "http.verify_ssl" in keys


def test_list_redacts_secrets_by_default(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "abcdef-secret")
    get_settings.cache_clear()

    result = CliRunner().invoke(
        app, ["list", "--format", "raw", "--columns", "key", "--columns", "value"]
    )
    assert result.exit_code == 0
    assert "abcdef-secret" not in result.stdout
    assert "demo.token\t***" in result.stdout


def test_list_show_secrets_reveals(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "abcdef-secret")
    get_settings.cache_clear()

    result = CliRunner().invoke(
        app,
        [
            "list",
            "--show-secrets",
            "--format",
            "raw",
            "--columns",
            "key",
            "--columns",
            "value",
        ],
    )
    assert result.exit_code == 0
    assert "abcdef-secret" in result.stdout


def test_set_then_list_shows_profile_default_source(_isolate_settings: Path) -> None:
    runner = CliRunner()
    set_result = runner.invoke(app, ["set", "log_level", "DEBUG"])
    assert set_result.exit_code == 0, set_result.output

    list_result = runner.invoke(
        app,
        [
            "list",
            "--format",
            "raw",
            "--columns",
            "key",
            "--columns",
            "value",
            "--columns",
            "source",
        ],
    )
    assert list_result.exit_code == 0
    assert "log_level\tDEBUG\tprofile:default" in list_result.stdout


def test_list_with_profile_flag_reads_named_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    log_level: WARNING\n"
        "  stage:\n    log_level: DEBUG\n"
        "active: prod\n"
    )

    result = CliRunner().invoke(
        app,
        [
            "list",
            "--profile",
            "stage",
            "--format",
            "raw",
            "--columns",
            "key",
            "--columns",
            "value",
            "--columns",
            "source",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "log_level\tDEBUG\tprofile:stage" in result.stdout
    assert yaml.safe_load(_isolate_settings.read_text())["active"] == "prod"


def test_list_rejects_profile_with_all_profiles(_isolate_settings: Path) -> None:
    result = CliRunner().invoke(app, ["list", "--all-profiles", "--profile", "stage"])

    assert result.exit_code == 2
    assert "Cannot combine --profile with --all-profiles" in result.output


def test_set_with_no_args_shows_help(_isolate_settings: Path) -> None:
    result = CliRunner().invoke(app, ["set"])
    assert result.exit_code == 2
    assert "key" in result.stdout.lower() or "key" in (result.output or "").lower()


def test_unset_with_no_args_shows_help(_isolate_settings: Path) -> None:
    result = CliRunner().invoke(app, ["unset"])
    assert result.exit_code == 2
    assert "key" in result.stdout.lower() or "key" in (result.output or "").lower()


def test_set_rejects_invalid_value(_isolate_settings: Path) -> None:
    result = CliRunner().invoke(app, ["set", "http.verify_ssl", "not-a-bool"])
    assert result.exit_code != 0


def test_set_rejects_unknown_key(_isolate_settings: Path) -> None:
    result = CliRunner().invoke(app, ["set", "bogus.key", "x"])
    assert result.exit_code != 0


def test_unset_returns_clean_when_not_set(_isolate_settings: Path) -> None:
    result = CliRunner().invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0


def test_unset_after_set(_isolate_settings: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["set", "log_level", "DEBUG"])
    result = runner.invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0
    list_result = runner.invoke(
        app, ["list", "--format", "raw", "--columns", "key", "--columns", "source"]
    )
    assert "log_level\tdefault" in list_result.stdout


def test_set_with_target_profile_flag_targets_named_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")
    runner = CliRunner()
    result = runner.invoke(app, ["set", "log_level", "DEBUG", "--target-profile", "prod"])
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["prod"]["log_level"] == "DEBUG"
    assert data["profiles"]["default"] == {}


def test_set_rejects_old_profile_flag(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")

    result = CliRunner().invoke(app, ["set", "log_level", "DEBUG", "--profile", "prod"])

    assert result.exit_code == 2
    assert "No such option: --profile" in result.output
    assert yaml.safe_load(_isolate_settings.read_text()) == {
        "profiles": {"default": {}, "prod": {}}
    }


def test_unset_rejects_old_profile_flag(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\n")

    result = CliRunner().invoke(app, ["unset", "log_level", "--profile", "default"])

    assert result.exit_code == 2
    assert "No such option: --profile" in result.output
    assert yaml.safe_load(_isolate_settings.read_text()) == {
        "profiles": {"default": {"log_level": "DEBUG"}}
    }


def test_set_with_unknown_profile_errors(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n")
    result = CliRunner().invoke(app, ["set", "log_level", "DEBUG", "--target-profile", "ghost"])
    assert result.exit_code != 0


def test_set_message_names_resolved_default_profile(_isolate_settings: Path) -> None:
    result = CliRunner().invoke(app, ["set", "log_level", "DEBUG"])
    assert result.exit_code == 0, result.output
    assert "in profile default" in result.output
    assert "<active>" not in result.output


def test_set_message_names_explicit_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")
    result = CliRunner().invoke(app, ["set", "log_level", "DEBUG", "--target-profile", "prod"])
    assert result.exit_code == 0, result.output
    assert "in profile prod" in result.output


def test_set_message_resolves_env_override(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  stage: {}\n")
    monkeypatch.setenv("UNTAPED_PROFILE", "stage")
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["set", "log_level", "DEBUG"])
    assert result.exit_code == 0, result.output
    assert "in profile stage" in result.output


def test_unset_message_names_resolved_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\n")
    result = CliRunner().invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0, result.output
    assert "in profile default" in result.output
    assert "<active>" not in result.output


def test_unset_with_missing_explicit_profile_errors(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n")
    result = CliRunner().invoke(app, ["unset", "log_level", "--target-profile", "ghost"])
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_unset_with_target_profile_flag_targets_named_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n  default:\n    log_level: INFO\n  prod:\n    log_level: DEBUG\nactive: prod\n"
    )

    result = CliRunner().invoke(app, ["unset", "log_level", "--target-profile", "default"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"] == {}
    assert data["profiles"]["prod"]["log_level"] == "DEBUG"


def test_unset_noop_message_names_resolved_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n")
    result = CliRunner().invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0, result.output
    assert "was not set in profile default" in result.output
    assert "<active>" not in result.output


def test_list_all_profiles_shows_per_profile_rows(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    log_level: DEBUG\n    demo:\n      base_url: https://p\n"
        "active: prod\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "list",
            "--all-profiles",
            "--format",
            "raw",
            "--columns",
            "profile",
            "--columns",
            "key",
            "--columns",
            "value",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = set(result.stdout.splitlines())
    assert "default\tlog_level\tINFO" in lines
    assert "prod\tlog_level\tDEBUG" in lines
    assert "prod\tdemo.base_url\thttps://p" in lines
