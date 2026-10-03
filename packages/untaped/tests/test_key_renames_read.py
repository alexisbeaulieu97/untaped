"""Renamed keys are read as their new names, from ``config.yml`` and the environment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from cyclopts import App
from pydantic import BaseModel, Field

from untaped import bootstrap
from untaped.capabilities.registry import CapabilitySpec
from untaped.cli import create_app
from untaped.errors import ConfigError
from untaped.profile.repository import ProfileFileRepository
from untaped.settings import (
    get_config_section,
    get_settings,
    load_settings_section,
    register_profile_settings,
    validate_config_file,
)
from untaped.testing import invoke_cli, provider_candidate


class Sweep(BaseModel):
    parallel: int = Field(default=12, ge=1)


class DemoSettings(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {
        "corpus_path": "cache_dir",
        "sweep.sync_concurrency": "sweep.parallel",
    }
    retired_keys: ClassVar[dict[str, str]] = {"ancient_path": "cache_dir"}
    deprecated_settings: ClassVar[dict[str, str]] = {"legacy": "use cache_dir: legacy is ignored"}

    cache_dir: str = "default-cache"
    legacy: bool = False
    sweep: Sweep = Field(default_factory=Sweep)


RENAMED = (
    "warning: demo.corpus_path is deprecated and will be removed in the next major release; "
    "use demo.cache_dir"
)


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(path))
    return path


def _write(path: Path, profiles: dict[str, object], active: str | None = None) -> None:
    data: dict[str, object] = {"profiles": profiles}
    if active:
        data["active"] = active
    path.write_text(yaml.safe_dump(data))


def test_old_key_in_default_and_new_key_in_active_layer_as_one_key(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    _write(
        config,
        {"default": {"demo": {"corpus_path": "/old"}}, "prod": {"demo": {"cache_dir": "/new"}}},
        active="prod",
    )

    assert get_config_section("demo", DemoSettings).cache_dir == "/new"
    assert RENAMED in capsys.readouterr().err


def test_old_key_is_read_with_a_warning_once_per_process(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    _write(config, {"default": {"demo": {"corpus_path": "/old"}}})

    assert get_config_section("demo", DemoSettings).cache_dir == "/old"
    assert load_settings_section("demo").cache_dir == "/old"
    get_settings.cache_clear()
    assert get_settings().demo.cache_dir == "/old"  # type: ignore[attr-defined]

    assert capsys.readouterr().err.count(RENAMED) == 1


def test_both_spellings_in_one_profile_the_new_key_wins(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    _write(config, {"default": {"demo": {"corpus_path": "/old", "cache_dir": "/new"}}})

    assert get_config_section("demo", DemoSettings).cache_dir == "/new"
    assert (
        "warning: demo.corpus_path is deprecated and ignored because demo.cache_dir is also set"
        in capsys.readouterr().err
    )


def test_old_keys_in_a_profile_not_read_do_not_warn(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    _write(config, {"default": {}, "other": {"demo": {"corpus_path": "/old"}}})

    assert get_config_section("demo", DemoSettings).cache_dir == "default-cache"
    assert capsys.readouterr().err == ""


def test_a_retired_key_is_not_read_and_not_warned(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    _write(config, {"default": {"demo": {"ancient_path": "/a"}}})

    assert get_config_section("demo", DemoSettings).cache_dir == "default-cache"
    assert capsys.readouterr().err == ""


def test_a_deprecated_setting_keeps_its_value_and_warns(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    _write(config, {"default": {"demo": {"legacy": True}}})

    assert get_config_section("demo", DemoSettings).legacy is True
    assert (
        "warning: demo.legacy is deprecated and will be removed in the next major release; "
        "use cache_dir: legacy is ignored"
    ) in capsys.readouterr().err


def test_an_unregistered_model_honours_its_declarations(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(config, {"default": {"demo": {"sweep": {"sync_concurrency": 3}}}})

    assert get_config_section("demo", DemoSettings).sweep.parallel == 3
    assert "demo.sweep.sync_concurrency is deprecated" in capsys.readouterr().err


def test_old_env_variable_is_read_without_the_migrate_hint(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    monkeypatch.setenv("UNTAPED_DEMO__CORPUS_PATH", "/env")

    assert get_config_section("demo", DemoSettings).cache_dir == "/env"
    err = capsys.readouterr().err
    assert (
        "warning: UNTAPED_DEMO__CORPUS_PATH is deprecated and will be removed in the next "
        "major release; use UNTAPED_DEMO__CACHE_DIR"
    ) in err
    assert "config migrate" not in err


def test_new_env_variable_wins_over_the_old_one(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    monkeypatch.setenv("UNTAPED_DEMO__CORPUS_PATH", "/old")
    monkeypatch.setenv("UNTAPED_DEMO__CACHE_DIR", "/new")

    assert get_config_section("demo", DemoSettings).cache_dir == "/new"
    assert (
        "warning: UNTAPED_DEMO__CORPUS_PATH is deprecated and ignored because "
        "UNTAPED_DEMO__CACHE_DIR is also set"
    ) in capsys.readouterr().err


def test_mixed_env_forms_name_where_each_value_is(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    monkeypatch.setenv("UNTAPED_DEMO", json.dumps({"cache_dir": "/blob"}))
    monkeypatch.setenv("UNTAPED_DEMO__CORPUS_PATH", "/old")

    assert get_config_section("demo", DemoSettings).cache_dir == "/blob"
    assert (
        "warning: UNTAPED_DEMO__CORPUS_PATH is deprecated and ignored because cache_dir in "
        "UNTAPED_DEMO is also set; use UNTAPED_DEMO__CACHE_DIR"
    ) in capsys.readouterr().err


def test_old_key_in_an_env_blob_names_the_blob(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    register_profile_settings("demo", DemoSettings)
    monkeypatch.setenv("UNTAPED_DEMO", json.dumps({"sweep": {"sync_concurrency": 5}}))

    assert get_config_section("demo", DemoSettings).sweep.parallel == 5
    assert (
        "warning: sweep.sync_concurrency in UNTAPED_DEMO is deprecated and will be removed "
        "in the next major release; use sweep.parallel"
    ) in capsys.readouterr().err


def test_an_invalid_old_spelling_variable_is_blamed(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_profile_settings("demo", DemoSettings)
    monkeypatch.setenv("UNTAPED_DEMO__SWEEP__SYNC_CONCURRENCY", "0")

    with pytest.raises(
        ConfigError, match="environment variable UNTAPED_DEMO__SWEEP__SYNC_CONCURRENCY"
    ):
        get_config_section("demo", DemoSettings)
    get_settings.cache_clear()
    with pytest.raises(ConfigError, match="UNTAPED_DEMO__SWEEP__SYNC_CONCURRENCY"):
        get_settings()


def test_validate_config_file_reads_old_keys(
    config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_profile_settings("demo", DemoSettings)
    candidate = tmp_path / "candidate.yml"
    _write(candidate, {"default": {"demo": {"sweep": {"sync_concurrency": 0}}}})

    with pytest.raises(ConfigError, match="invalid config section 'demo'"):
        validate_config_file(candidate)

    _write(candidate, {"default": {"demo": {"sweep": {"sync_concurrency": 2}}}})
    validate_config_file(candidate)

    monkeypatch.setenv("UNTAPED_DEMO__SWEEP__SYNC_CONCURRENCY", "0")
    with pytest.raises(ConfigError, match="UNTAPED_DEMO__SWEEP__SYNC_CONCURRENCY"):
        validate_config_file(candidate)


def test_profile_show_resolves_old_keys_to_new_names(config: Path) -> None:
    register_profile_settings("demo", DemoSettings)
    _write(config, {"default": {"demo": {"corpus_path": "/old"}}, "prod": {}})

    assert ProfileFileRepository().resolved("prod") == {"demo": {"cache_dir": "/old"}}


def _demo_root() -> object:
    def _factory() -> App:
        app = create_app(name="demo", help="demo capability.")

        @app.command
        def show(*, format: str = "table") -> None:
            """Print the cache directory."""
            print(get_config_section("demo", DemoSettings).cache_dir)

        return app

    spec = CapabilitySpec(
        name="demo", app_factory=_factory, config_section="demo", profile_model=DemoSettings
    )
    return bootstrap.build_root_app(candidates=(provider_candidate(spec),)).meta


@pytest.mark.parametrize("flags", [[], ["--quiet"]])
def test_the_warning_survives_quiet(config: Path, flags: list[str]) -> None:
    _write(config, {"default": {"demo": {"corpus_path": "/old"}}})

    result = invoke_cli(_demo_root(), [*flags, "demo", "show"])

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "/old"
    assert RENAMED in result.stderr


def test_the_warning_is_a_json_diagnostic(config: Path) -> None:
    _write(config, {"default": {"demo": {"corpus_path": "/old"}}})

    result = invoke_cli(_demo_root(), ["demo", "show", "--format", "json"])

    lines = [json.loads(line) for line in result.stderr.splitlines()]
    assert {"level": "warning", "message": RENAMED.removeprefix("warning: ")}.items() <= lines[
        0
    ].items()
