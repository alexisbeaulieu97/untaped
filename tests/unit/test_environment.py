from pathlib import Path

import pytest

from untaped.environment import environment_check, managed_env_mismatch
from untaped.errors import ConfigError
from untaped.settings import PluginInstallSpec, PluginsState


def _raising_state_reader() -> PluginsState:
    raise AssertionError("state must not be read on the cheap paths")


def _state_with_packages(count: int = 1) -> PluginsState:
    return PluginsState(
        packages=[PluginInstallSpec(spec=f"untaped-plugin-{i}") for i in range(count)]
    )


def _managed_venv(tmp_path: Path) -> Path:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").touch()
    return venv


def test_mismatch_silent_when_running_inside_managed_venv(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    assert (
        managed_env_mismatch(
            prefix=venv,
            venv=venv,
            loaded_plugin_count=0,
            state_reader=_raising_state_reader,
        )
        is None
    )


def test_mismatch_silent_when_plugins_loaded(tmp_path: Path) -> None:
    assert (
        managed_env_mismatch(
            prefix=tmp_path / "other",
            venv=_managed_venv(tmp_path),
            loaded_plugin_count=2,
            state_reader=_raising_state_reader,
        )
        is None
    )


def test_mismatch_silent_when_no_managed_venv_exists(tmp_path: Path) -> None:
    assert (
        managed_env_mismatch(
            prefix=tmp_path / "other",
            venv=tmp_path / "missing-venv",
            loaded_plugin_count=0,
            state_reader=_raising_state_reader,
        )
        is None
    )


def test_mismatch_silent_when_state_is_unreadable(tmp_path: Path) -> None:
    def broken() -> PluginsState:
        raise ConfigError("invalid plugins config")

    assert (
        managed_env_mismatch(
            prefix=tmp_path / "other",
            venv=_managed_venv(tmp_path),
            loaded_plugin_count=0,
            state_reader=broken,
        )
        is None
    )


def test_mismatch_silent_when_no_packages_recorded(tmp_path: Path) -> None:
    assert (
        managed_env_mismatch(
            prefix=tmp_path / "other",
            venv=_managed_venv(tmp_path),
            loaded_plugin_count=0,
            state_reader=PluginsState,
        )
        is None
    )


def test_mismatch_warns_when_recorded_plugins_cannot_load(tmp_path: Path) -> None:
    prefix = tmp_path / "uv-tools-env"
    message = managed_env_mismatch(
        prefix=prefix,
        venv=_managed_venv(tmp_path),
        loaded_plugin_count=0,
        state_reader=lambda: _state_with_packages(3),
    )
    assert message is not None
    assert message.startswith("warning: ")
    assert "3 plugins recorded" in message
    assert str(prefix) in message
    assert "scripts/install.sh" in message
    assert "uv tool uninstall untaped" in message


def test_mismatch_warning_uses_singular_for_one_plugin(tmp_path: Path) -> None:
    message = managed_env_mismatch(
        prefix=tmp_path / "other",
        venv=_managed_venv(tmp_path),
        loaded_plugin_count=0,
        state_reader=lambda: _state_with_packages(1),
    )
    assert message is not None
    assert "1 plugin recorded" in message


def _write_shim(tmp_path: Path, venv: Path) -> Path:
    shim = tmp_path / "bin" / "untaped"
    shim.parent.mkdir(parents=True)
    target = venv / "bin" / "untaped"
    shim.write_text(f'#!/usr/bin/env sh\nexec {target} "$@"\n')
    return shim


def test_environment_check_ok_when_everything_agrees(tmp_path: Path) -> None:
    venv = _managed_venv(tmp_path)
    result = environment_check(
        prefix=venv,
        venv=venv,
        shim=_write_shim(tmp_path, venv),
        state_reader=lambda: _state_with_packages(2),
    )
    assert result.name == "core-environment"
    assert result.status == "ok"


def test_environment_check_errors_on_interpreter_mismatch(tmp_path: Path) -> None:
    venv = _managed_venv(tmp_path)
    prefix = tmp_path / "uv-tools-env"
    result = environment_check(
        prefix=prefix,
        venv=venv,
        shim=_write_shim(tmp_path, venv),
        state_reader=lambda: _state_with_packages(2),
    )
    assert result.status == "error"
    assert str(prefix) in result.detail
    assert str(venv) in result.detail
    assert "scripts/install.sh" in result.detail


def test_environment_check_ok_on_mismatch_without_recorded_plugins(tmp_path: Path) -> None:
    venv = _managed_venv(tmp_path)
    result = environment_check(
        prefix=tmp_path / "dev-venv",
        venv=venv,
        shim=_write_shim(tmp_path, venv),
        state_reader=PluginsState,
    )
    assert result.status == "ok"


def test_environment_check_errors_on_foreign_shim(tmp_path: Path) -> None:
    venv = _managed_venv(tmp_path)
    shim = tmp_path / "bin" / "untaped"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/usr/bin/env python3\n# uv tool shim\n")
    result = environment_check(
        prefix=venv,
        venv=venv,
        shim=shim,
        state_reader=PluginsState,
    )
    assert result.status == "error"
    assert str(shim) in result.detail
    assert "scripts/install.sh" in result.detail


def test_environment_check_ignores_shim_without_managed_venv(tmp_path: Path) -> None:
    """A foreign shim is fine when no managed install exists to delegate to."""
    venv = tmp_path / "missing-venv"
    shim = tmp_path / "bin" / "untaped"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/usr/bin/env python3\n# uv tool shim\n")
    result = environment_check(
        prefix=tmp_path / "uv-tools-env",
        venv=venv,
        shim=shim,
        state_reader=PluginsState,
    )
    assert result.status == "ok"


def test_environment_check_ok_without_shim(tmp_path: Path) -> None:
    venv = _managed_venv(tmp_path)
    result = environment_check(
        prefix=venv,
        venv=venv,
        shim=tmp_path / "bin" / "untaped",
        state_reader=PluginsState,
    )
    assert result.status == "ok"


def test_environment_check_reports_unreadable_state(tmp_path: Path) -> None:
    venv = _managed_venv(tmp_path)

    def broken() -> PluginsState:
        raise ConfigError("invalid plugins config: boom")

    result = environment_check(
        prefix=venv,
        venv=venv,
        shim=tmp_path / "bin" / "untaped",
        state_reader=broken,
    )
    assert result.status == "error"
    assert "invalid plugins config: boom" in result.detail


@pytest.mark.parametrize("count", [1, 2])
def test_mismatch_counts_recorded_packages(tmp_path: Path, count: int) -> None:
    message = managed_env_mismatch(
        prefix=tmp_path / "other",
        venv=_managed_venv(tmp_path),
        loaded_plugin_count=0,
        state_reader=lambda: _state_with_packages(count),
    )
    assert message is not None
    assert f"{count} plugin" in message
