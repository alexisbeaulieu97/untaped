"""Unit tests for inter-plugin dependency discovery on local plugin specs."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.errors import ConfigError
from untaped.plugin_deps import (
    PluginDependency,
    PluginDepSource,
    dependency_install_spec,
    expand_plugin_dependencies,
    local_plugin_dependencies,
)
from untaped.settings import PluginInstallSpec


def _write_plugin(
    path: Path,
    name: str,
    *,
    dependencies: list[str] | None = None,
    sources: dict[str, str] | None = None,
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    deps = "".join(f"    {dep!r},\n" for dep in dependencies or [])
    body = f'[project]\nname = "{name}"\nversion = "0.1.0"\ndependencies = [\n{deps}]\n'
    if sources:
        body += "\n[tool.uv.sources]\n"
        for dep_name, table in sources.items():
            body += f"{dep_name} = {table}\n"
    (path / "pyproject.toml").write_text(body)
    return path


class TestLocalPluginDependencies:
    def test_collects_untaped_plugin_deps_with_sources(self, tmp_path: Path) -> None:
        project = _write_plugin(
            tmp_path / "untaped-ansible",
            "untaped-ansible",
            dependencies=["cyclopts>=4.16", "untaped>=0.3.0", "untaped-github>=0.3.0"],
            sources={
                "untaped": '{ git = "https://github.com/example/untaped" }',
                "untaped-github": '{ path = "../untaped-github" }',
            },
        )
        deps = local_plugin_dependencies(project)
        assert deps == [
            PluginDependency(
                name="untaped-github",
                requirement="untaped-github>=0.3.0",
                source=PluginDepSource(kind="path", target="../untaped-github"),
            )
        ]

    def test_ignores_non_plugin_and_core_dependencies(self, tmp_path: Path) -> None:
        project = _write_plugin(
            tmp_path / "plugin",
            "untaped-example",
            dependencies=["pydantic>=2", "untaped>=0.3.0"],
        )
        assert local_plugin_dependencies(project) == []

    def test_dependency_without_source_has_none(self, tmp_path: Path) -> None:
        project = _write_plugin(
            tmp_path / "plugin",
            "untaped-example",
            dependencies=["untaped-github>=0.3.0"],
        )
        deps = local_plugin_dependencies(project)
        assert deps == [
            PluginDependency(
                name="untaped-github",
                requirement="untaped-github>=0.3.0",
                source=None,
            )
        ]

    def test_git_source_with_rev(self, tmp_path: Path) -> None:
        project = _write_plugin(
            tmp_path / "plugin",
            "untaped-ansible",
            dependencies=["untaped-github>=0.3.0"],
            sources={
                "untaped-github": ('{ git = "file:///dev/untaped-github", rev = "c64967a" }'),
            },
        )
        deps = local_plugin_dependencies(project)
        assert deps[0].source == PluginDepSource(
            kind="git", target="file:///dev/untaped-github", rev="c64967a"
        )

    def test_editable_path_source(self, tmp_path: Path) -> None:
        project = _write_plugin(
            tmp_path / "plugin",
            "untaped-ansible",
            dependencies=["untaped-github"],
            sources={"untaped-github": '{ path = "../untaped-github", editable = true }'},
        )
        deps = local_plugin_dependencies(project)
        assert deps[0].source == PluginDepSource(
            kind="path", target="../untaped-github", editable=True
        )

    def test_unsupported_source_kind_is_treated_as_no_source(self, tmp_path: Path) -> None:
        project = _write_plugin(
            tmp_path / "plugin",
            "untaped-ansible",
            dependencies=["untaped-github"],
            sources={"untaped-github": "{ workspace = true }"},
        )
        deps = local_plugin_dependencies(project)
        assert deps[0].source is None

    def test_missing_pyproject_returns_no_dependencies(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert local_plugin_dependencies(empty) == []


class TestDependencyInstallSpec:
    def test_path_source_resolves_against_plugin_dir(self, tmp_path: Path) -> None:
        dep_project = _write_plugin(tmp_path / "untaped-github", "untaped-github")
        parent_dir = tmp_path / "untaped-ansible"
        parent_dir.mkdir()
        dep = PluginDependency(
            name="untaped-github",
            requirement="untaped-github>=0.3.0",
            source=PluginDepSource(kind="path", target="../untaped-github"),
        )
        spec = dependency_install_spec(dep, base_dir=parent_dir)
        assert spec == PluginInstallSpec(
            spec=str(dep_project.resolve()),
            editable=False,
            name="untaped-github",
        )

    def test_editable_path_source_records_editable(self, tmp_path: Path) -> None:
        dep_project = _write_plugin(tmp_path / "untaped-github", "untaped-github")
        dep = PluginDependency(
            name="untaped-github",
            requirement="untaped-github",
            source=PluginDepSource(kind="path", target="untaped-github", editable=True),
        )
        spec = dependency_install_spec(dep, base_dir=tmp_path)
        assert spec is not None
        assert spec.editable is True
        assert spec.spec == str(dep_project.resolve())

    def test_missing_path_source_raises_config_error(self, tmp_path: Path) -> None:
        dep = PluginDependency(
            name="untaped-github",
            requirement="untaped-github",
            source=PluginDepSource(kind="path", target="../nope"),
        )
        with pytest.raises(ConfigError, match="untaped-github"):
            dependency_install_spec(dep, base_dir=tmp_path)

    def test_git_source_builds_direct_reference(self, tmp_path: Path) -> None:
        dep = PluginDependency(
            name="untaped-github",
            requirement="untaped-github>=0.3.0",
            source=PluginDepSource(
                kind="git", target="https://github.com/example/untaped-github", rev="abc123"
            ),
        )
        spec = dependency_install_spec(dep, base_dir=tmp_path)
        assert spec == PluginInstallSpec(
            spec="untaped-github @ git+https://github.com/example/untaped-github@abc123",
            editable=False,
            name="untaped-github",
        )

    def test_git_source_without_rev(self, tmp_path: Path) -> None:
        dep = PluginDependency(
            name="untaped-github",
            requirement="untaped-github",
            source=PluginDepSource(kind="git", target="file:///dev/untaped-github"),
        )
        spec = dependency_install_spec(dep, base_dir=tmp_path)
        assert spec is not None
        assert spec.spec == "untaped-github @ git+file:///dev/untaped-github"

    def test_no_source_resolves_to_none(self, tmp_path: Path) -> None:
        dep = PluginDependency(
            name="untaped-github",
            requirement="untaped-github>=0.3.0",
            source=None,
        )
        assert dependency_install_spec(dep, base_dir=tmp_path) is None


class TestExpandPluginDependencies:
    def test_expands_transitive_chain(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path / "untaped-c", "untaped-c")
        _write_plugin(
            tmp_path / "untaped-b",
            "untaped-b",
            dependencies=["untaped-c"],
            sources={"untaped-c": '{ path = "../untaped-c" }'},
        )
        top = _write_plugin(
            tmp_path / "untaped-a",
            "untaped-a",
            dependencies=["untaped-b"],
            sources={"untaped-b": '{ path = "../untaped-b" }'},
        )
        top_spec = PluginInstallSpec(spec=str(top), name="untaped-a")
        expanded = expand_plugin_dependencies([top_spec], already_recorded=set())
        assert [(spec.name, parent) for spec, parent in expanded] == [
            ("untaped-b", "untaped-a"),
            ("untaped-c", "untaped-b"),
        ]

    def test_cycle_terminates(self, tmp_path: Path) -> None:
        a = _write_plugin(
            tmp_path / "untaped-a",
            "untaped-a",
            dependencies=["untaped-b"],
            sources={"untaped-b": '{ path = "../untaped-b" }'},
        )
        _write_plugin(
            tmp_path / "untaped-b",
            "untaped-b",
            dependencies=["untaped-a"],
            sources={"untaped-a": '{ path = "../untaped-a" }'},
        )
        top_spec = PluginInstallSpec(spec=str(a), name="untaped-a")
        expanded = expand_plugin_dependencies([top_spec], already_recorded=set())
        assert [spec.name for spec, _ in expanded] == ["untaped-b"]

    def test_skips_already_recorded_dependencies(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path / "untaped-github", "untaped-github")
        top = _write_plugin(
            tmp_path / "untaped-ansible",
            "untaped-ansible",
            dependencies=["untaped-github"],
            sources={"untaped-github": '{ path = "../untaped-github" }'},
        )
        top_spec = PluginInstallSpec(spec=str(top), name="untaped-ansible")
        expanded = expand_plugin_dependencies([top_spec], already_recorded={"untaped-github"})
        assert expanded == []

    def test_non_local_specs_are_not_inspected(self, tmp_path: Path) -> None:
        spec = PluginInstallSpec(spec="untaped-awx>=0.3.0")
        assert expand_plugin_dependencies([spec], already_recorded=set()) == []

    def test_requested_specs_are_not_duplicated_as_dependencies(self, tmp_path: Path) -> None:
        github = _write_plugin(tmp_path / "untaped-github", "untaped-github")
        ansible = _write_plugin(
            tmp_path / "untaped-ansible",
            "untaped-ansible",
            dependencies=["untaped-github"],
            sources={"untaped-github": '{ path = "../untaped-github" }'},
        )
        specs = [
            PluginInstallSpec(spec=str(ansible), name="untaped-ansible"),
            PluginInstallSpec(spec=str(github), name="untaped-github"),
        ]
        assert expand_plugin_dependencies(specs, already_recorded=set()) == []
