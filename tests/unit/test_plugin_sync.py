"""Tests for managed plugin environment sync helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from untaped.errors import ConfigError
from untaped.install_paths import default_managed_venv_path
from untaped.plugin_sync import sync_state_unlocked, uv_pip_compile_command, venv_python
from untaped.settings import PluginInstallSpec, PluginsState, PluginToolSpec


def test_uv_compile_command_ignores_all_uv_sources(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.in"
    resolved = tmp_path / "requirements.txt"

    command = uv_pip_compile_command(
        Path(sys.executable),
        requirements,
        resolved,
    )

    assert "--no-sources" in command
    assert "--no-sources-package" not in command


def test_uv_compile_resolves_transitive_direct_plugin_dependency(tmp_path: Path) -> None:
    child = _write_wheel(tmp_path, name="untaped-github-fixture")
    parent = _write_wheel(
        tmp_path,
        name="untaped-ansible-fixture",
        requires_dist=f"untaped-github-fixture @ {child.as_uri()}",
    )
    core = _write_wheel(tmp_path, name="untaped")
    requirements = tmp_path / "requirements.in"
    resolved = tmp_path / "requirements.txt"
    requirements.write_text(f"{core}\n{parent}\n", encoding="utf-8")

    result = subprocess.run(
        [
            *uv_pip_compile_command(Path(sys.executable), requirements, resolved),
            "--no-index",
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "untaped-github-fixture" not in requirements.read_text(encoding="utf-8")
    assert "untaped-github-fixture" in resolved.read_text(encoding="utf-8")


def test_uv_compile_ignores_editable_plugin_uv_sources_for_explicit_core(
    tmp_path: Path,
) -> None:
    core = _write_local_project(tmp_path, name="untaped", version="0.1.0")
    plugin = _write_local_project(
        tmp_path,
        name="untaped-local-plugin",
        version="0.1.0",
        dependencies=["untaped>=0.1.0"],
        uv_sources={"untaped": '{ git = "https://example.invalid/untaped.git" }'},
    )
    requirements = tmp_path / "requirements.in"
    resolved = tmp_path / "requirements.txt"
    requirements.write_text(f"-e {core}\n-e {plugin}\n", encoding="utf-8")

    result = subprocess.run(
        uv_pip_compile_command(Path(sys.executable), requirements, resolved),
        check=False,
        capture_output=True,
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    resolved_text = resolved.read_text(encoding="utf-8")
    assert "untaped-local-plugin" in resolved_text
    assert "example.invalid" not in resolved_text


def test_uv_compile_does_not_resolve_dependencies_from_plugin_uv_sources(
    tmp_path: Path,
) -> None:
    core = _write_local_project(tmp_path, name="untaped", version="0.1.0")
    helper = _write_local_project(
        tmp_path,
        name="untaped-helper-fixture",
        version="0.1.0",
    )
    plugin = _write_local_project(
        tmp_path,
        name="untaped-local-plugin",
        version="0.1.0",
        dependencies=[
            "untaped>=0.1.0",
            "untaped-helper-fixture>=0.1.0",
        ],
        uv_sources={
            "untaped-helper-fixture": f'{{ path = "{helper.as_posix()}" }}',
        },
    )
    requirements = tmp_path / "requirements.in"
    resolved = tmp_path / "requirements.txt"
    requirements.write_text(f"-e {core}\n-e {plugin}\n", encoding="utf-8")

    result = subprocess.run(
        [
            *uv_pip_compile_command(Path(sys.executable), requirements, resolved),
            "--no-index",
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")},
        text=True,
    )

    assert result.returncode != 0
    assert "untaped-helper-fixture" in f"{result.stdout}\n{result.stderr}"


def _write_local_project(
    root: Path,
    *,
    name: str,
    version: str,
    dependencies: list[str] | None = None,
    uv_sources: dict[str, str] | None = None,
) -> Path:
    project = root / name
    package = project / "src" / name.replace("-", "_")
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    dependency_lines = "".join(f'    "{dependency}",\n' for dependency in dependencies or [])
    pyproject = (
        "[project]\n"
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = [\n"
        f"{dependency_lines}"
        "]\n\n"
        "[build-system]\n"
        'requires = ["uv_build>=0.11.16,<0.12.0"]\n'
        'build-backend = "uv_build"\n'
    )
    if uv_sources:
        pyproject += "\n[tool.uv.sources]\n"
        pyproject += "".join(
            f"{package_name} = {source}\n" for package_name, source in uv_sources.items()
        )
    (project / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return project


def _write_wheel(
    root: Path,
    *,
    name: str,
    version: str = "0.1.0",
    requires_dist: str | None = None,
) -> Path:
    normalized = name.replace("-", "_")
    wheel = root / f"{normalized}-{version}-py3-none-any.whl"
    dist_info = f"{normalized}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    if requires_dist is not None:
        metadata += f"Requires-Dist: {requires_dist}\n"
    with ZipFile(wheel, "w", ZIP_DEFLATED) as archive:
        archive.writestr(f"{dist_info}/METADATA", metadata)
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: untaped-tests\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return wheel


def test_compile_failure_hints_at_explicit_plugin_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = venv_python(default_managed_venv_path())
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()

    def _fail(cmd: list[str], **_: object) -> object:
        return type("Result", (), {"returncode": 1})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _fail)
    state = PluginsState(
        tool=PluginToolSpec(spec="untaped"),
        packages=[PluginInstallSpec(spec="untaped-ansible")],
    )

    with pytest.raises(ConfigError) as excinfo:
        sync_state_unlocked(state)

    message = str(excinfo.value)
    assert "plugin dependency resolution failed" in message
    assert "hint:" in message
    assert "untaped plugins add" in message
