from __future__ import annotations

from pathlib import Path

import pytest
import typer
from pydantic import BaseModel, SecretStr

from untaped.errors import ConfigError
from untaped.plugins import DiagnosticResult, PluginRegistry, SkillSpec, register_plugins
from untaped.ui import ThemeSpec


class DemoSettings(BaseModel):
    token: SecretStr | None = None


class DemoState(BaseModel):
    entries: list[str] = []


class OverlappingDemoState(BaseModel):
    token: str | None = None


def test_registry_rejects_duplicate_plugin_ids() -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("demo")

    with pytest.raises(ConfigError, match="duplicate plugin id"):
        registry.add_plugin_id("demo")


def test_registry_rejects_duplicate_cli_names() -> None:
    registry = PluginRegistry()
    registry.add_cli("demo", typer.Typer())

    with pytest.raises(ConfigError, match="duplicate CLI command"):
        registry.add_cli("demo", typer.Typer())


def test_registry_rejects_reserved_cli_names() -> None:
    registry = PluginRegistry(reserved_cli_names={"config"})

    with pytest.raises(ConfigError, match="reserved CLI command"):
        registry.add_cli("config", typer.Typer())


def test_register_plugins_restores_plugin_ids_after_duplicate_failure() -> None:
    class DemoPlugin:
        id = "demo"

        def __init__(self, command: str) -> None:
            self.command = command

        def register(self, registry: PluginRegistry) -> None:
            registry.add_cli(self.command, typer.Typer())

    registry = PluginRegistry()

    register_plugins(
        registry,
        [
            DemoPlugin("first"),
            DemoPlugin("second"),
            DemoPlugin("third"),
        ],
    )

    assert registry.plugin_ids == {"demo"}
    assert sorted(registry.clis) == ["first"]
    assert [error.name for error in registry.load_errors] == ["demo", "demo"]


def test_registry_rejects_duplicate_profile_setting_sections() -> None:
    registry = PluginRegistry()
    registry.add_profile_settings("demo", DemoSettings)

    with pytest.raises(ConfigError, match="duplicate profile settings section"):
        registry.add_profile_settings("demo", DemoSettings)


def test_registry_rejects_duplicate_state_setting_sections() -> None:
    registry = PluginRegistry()
    registry.add_state_settings("demo", DemoState)

    with pytest.raises(ConfigError, match="duplicate state settings section"):
        registry.add_state_settings("demo", DemoState)


def test_registry_rejects_reserved_builtin_state_sections() -> None:
    registry = PluginRegistry()

    with pytest.raises(ConfigError, match="reserved state settings section"):
        registry.add_state_settings("ui", DemoState)


def test_registry_rejects_reserved_builtin_profile_sections() -> None:
    registry = PluginRegistry()

    with pytest.raises(ConfigError, match="reserved profile settings section"):
        registry.add_profile_settings("ui", DemoSettings)


def test_registry_rejects_overlapping_profile_and_state_setting_fields() -> None:
    registry = PluginRegistry()
    registry.add_profile_settings("demo", DemoSettings)

    with pytest.raises(ConfigError, match="overlapping profile/state settings"):
        registry.add_state_settings("demo", OverlappingDemoState)


def test_registry_stores_diagnostics() -> None:
    registry = PluginRegistry()
    registry.add_diagnostic("demo", lambda: DiagnosticResult(name="demo", status="ok"))

    assert registry.run_diagnostics() == [DiagnosticResult(name="demo", status="ok")]


def test_registry_rejects_duplicate_theme_names() -> None:
    registry = PluginRegistry()
    registry.add_theme("demo", ThemeSpec(border="square"))

    with pytest.raises(ConfigError, match="duplicate theme"):
        registry.add_theme("demo", ThemeSpec(border="rounded"))


def test_registry_rejects_builtin_theme_names() -> None:
    registry = PluginRegistry()

    with pytest.raises(ConfigError, match="reserved theme"):
        registry.add_theme("default", ThemeSpec(border="square"))


def test_register_plugins_restores_themes_after_failure() -> None:
    class BrokenThemePlugin:
        id = "broken"

        def register(self, registry: PluginRegistry) -> None:
            registry.add_theme("broken", ThemeSpec(border="square"))
            raise ConfigError("boom")

    registry = PluginRegistry()

    register_plugins(registry, [BrokenThemePlugin()])

    assert registry.themes == {}
    assert [error.name for error in registry.load_errors] == ["broken"]


def _skill_dir(tmp_path: Path, name: str = "untaped-demo") -> Path:
    source = tmp_path / name
    source.mkdir()
    source.joinpath("SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Teach agents how to use demo untaped commands.\n"
        "---\n"
        "\n"
        "# Demo\n",
    )
    return source


def test_registry_stores_skill_specs(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    registry = PluginRegistry()

    registry.add_skill(
        SkillSpec(
            name="untaped-demo",
            source=source,
            description="Teach agents how to use demo untaped commands.",
        )
    )

    assert registry.skills["untaped-demo"].source == source


def test_registry_rejects_unprefixed_skill_names(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path, name="demo")
    registry = PluginRegistry()

    with pytest.raises(ConfigError, match="skill name must be 'untaped' or start with 'untaped-'"):
        registry.add_skill(
            SkillSpec(
                name="demo",
                source=source,
                description="Teach agents how to use demo commands.",
            )
        )


def test_registry_rejects_duplicate_skill_names(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    registry = PluginRegistry()
    spec = SkillSpec(
        name="untaped-demo",
        source=source,
        description="Teach agents how to use demo commands.",
    )
    registry.add_skill(spec)

    with pytest.raises(ConfigError, match="duplicate skill"):
        registry.add_skill(spec)


def test_registry_rejects_skill_directory_without_skill_md(tmp_path: Path) -> None:
    source = tmp_path / "untaped-demo"
    source.mkdir()
    registry = PluginRegistry()

    with pytest.raises(ConfigError, match=r"skill source must contain SKILL\.md"):
        registry.add_skill(
            SkillSpec(
                name="untaped-demo",
                source=source,
                description="Teach agents how to use demo commands.",
            )
        )


def test_registry_rejects_skill_with_mismatched_frontmatter_name(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path, name="untaped-other")
    registry = PluginRegistry()

    with pytest.raises(ConfigError, match=r"SKILL\.md name must match skill name"):
        registry.add_skill(
            SkillSpec(
                name="untaped-demo",
                source=source,
                description="Teach agents how to use demo commands.",
            )
        )


def test_registry_rejects_skill_with_unclosed_frontmatter(tmp_path: Path) -> None:
    source = tmp_path / "untaped-demo"
    source.mkdir()
    source.joinpath("SKILL.md").write_text(
        "---\n"
        "name: untaped-demo\n"
        "description: Teach agents how to use demo untaped commands.\n"
        "\n"
        "# Demo\n",
    )
    registry = PluginRegistry()

    with pytest.raises(ConfigError, match=r"SKILL\.md frontmatter is not closed"):
        registry.add_skill(
            SkillSpec(
                name="untaped-demo",
                source=source,
                description="Teach agents how to use demo commands.",
            )
        )


def test_register_plugins_restores_skills_after_failure(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)

    class BrokenSkillPlugin:
        id = "broken"

        def register(self, registry: PluginRegistry) -> None:
            registry.add_skill(
                SkillSpec(
                    name="untaped-demo",
                    source=source,
                    description="Teach agents how to use demo commands.",
                )
            )
            raise ConfigError("boom")

    registry = PluginRegistry()

    register_plugins(registry, [BrokenSkillPlugin()])

    assert registry.skills == {}
    assert [error.name for error in registry.load_errors] == ["broken"]
