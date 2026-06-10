"""Agent skill commands and install helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    existing_directory,
    report_errors,
)
from untaped.errors import ConfigError
from untaped.plugin_registry import PluginRegistry, SkillSpec, current_registry
from untaped.stdin import read_identifiers
from untaped.ui import OutputFormat, UiContext, ui_context

CORE_SKILL_NAME = "untaped"
CORE_SKILL_DESCRIPTION = "Use the untaped CLI, configuration model, and plugin system."


class SkillInstallTarget(StrEnum):
    """Supported agent skill installation targets."""

    codex = "codex"
    claude = "claude"
    all = "all"


class SkillInstallScope(StrEnum):
    """Supported agent skill installation scopes."""

    global_ = "global"
    local = "local"


@dataclass(frozen=True)
class SkillInstallDestination:
    """Resolved skill installation destination."""

    target: str
    scope: SkillInstallScope
    root: Path


SkillNamesArgument = Annotated[
    list[str] | None,
    Parameter(help="Skill name(s) to install."),
]
SkillStdinOption = Annotated[
    bool,
    Parameter(name="--stdin", help="Read skill names from stdin."),
]
AllSkillsOption = Annotated[
    bool,
    Parameter(name="--all", help="Install every registered skill."),
]
SkillTargetOption = Annotated[
    SkillInstallTarget,
    Parameter(
        name="--target",
        help="Agent target to install for.",
    ),
]
SkillForceOption = Annotated[
    bool,
    Parameter(name="--force", help="Replace existing target skill dirs."),
]
SkillScopeOption = Annotated[
    SkillInstallScope,
    Parameter(
        name="--scope",
        help="Install scope.",
    ),
]
SkillProjectDirOption = Annotated[
    Path | None,
    Parameter(
        name="--project-dir",
        help="Project directory for local installs.",
        validator=existing_directory,
    ),
]
SkillTargetDirOption = Annotated[
    Path | None,
    Parameter(
        name="--target-dir",
        help="Override the selected target's skills directory.",
    ),
]


app = create_app(
    name="skills",
    help="List and install agent skills contributed by untaped plugins.",
)


@app.command(name="list")
def list_command(
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List registered agent skills."""
    with report_errors():
        rows = skill_rows(current_registry())
        rendered = _render_collection(rows, fmt=fmt, columns=columns)
        if rendered:
            echo(rendered)


@app.command(name="install")
def install_command(
    skill_names: SkillNamesArgument = None,
    *,
    stdin: SkillStdinOption = False,
    all_skills: AllSkillsOption = False,
    target: SkillTargetOption = SkillInstallTarget.codex,
    force: SkillForceOption = False,
    scope: SkillScopeOption = SkillInstallScope.global_,
    project_dir: SkillProjectDirOption = None,
    target_dir: SkillTargetDirOption = None,
) -> None:
    """Install registered skills into an agent skill directory."""
    if (
        not skill_names
        and not stdin
        and not all_skills
        and target == SkillInstallTarget.codex
        and not force
        and scope == SkillInstallScope.global_
        and project_dir is None
        and target_dir is None
    ):
        app.help_print(["install"])
        raise SystemExit()
    with report_errors():
        registry = current_registry()
        selected_names = _selected_skill_names(
            registry,
            list(skill_names or []),
            stdin=stdin,
            all_skills=all_skills,
        )
        targets = _install_targets(
            target,
            scope=scope,
            project_dir=project_dir,
            target_dir=target_dir,
        )
        install_plan = _plan_install(registry, selected_names, targets, force=force)
        for spec, target_destination, destination in install_plan:
            _install_skill(
                spec,
                target_destination=target_destination,
                destination=destination,
                force=force,
            )
            ui_context(strict=False).message("success", f"installed skill: {spec.name}")


def register_builtin_skills(registry: PluginRegistry) -> None:
    """Register core-owned packaged skills."""
    registry.add_skill(
        SkillSpec(
            name=CORE_SKILL_NAME,
            source=Path(str(files("untaped").joinpath("skill_assets", CORE_SKILL_NAME))),
            description=CORE_SKILL_DESCRIPTION,
        )
    )


def skill_rows(registry: PluginRegistry) -> list[dict[str, object]]:
    """Return deterministic row data for registered skills."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "source": str(spec.source),
        }
        for spec in sorted(registry.skills.values(), key=lambda item: item.name)
    ]


def _selected_skill_names(
    registry: PluginRegistry,
    skill_names: list[str],
    *,
    stdin: bool,
    all_skills: bool,
) -> list[str]:
    selector_count = int(bool(skill_names)) + int(stdin) + int(all_skills)
    if selector_count != 1:
        raise ConfigError("provide skill names, --stdin, or --all; not more than one")
    selected = sorted(registry.skills) if all_skills else read_identifiers(skill_names, stdin=stdin)
    duplicate = _first_duplicate(selected)
    if duplicate is not None:
        raise ConfigError(f"duplicate skill selected: {duplicate}")
    missing = [name for name in selected if name not in registry.skills]
    if len(missing) == 1:
        raise ConfigError(f"unknown skill: {missing[0]}")
    if missing:
        raise ConfigError(f"unknown skills: {', '.join(missing)}")
    return selected


def _first_duplicate(values: list[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _install_targets(
    target: SkillInstallTarget,
    *,
    scope: SkillInstallScope,
    project_dir: Path | None,
    target_dir: Path | None,
) -> list[SkillInstallDestination]:
    if target_dir is not None and target == SkillInstallTarget.all:
        raise ConfigError("--target-dir requires --target codex or --target claude")
    if project_dir is not None and scope != SkillInstallScope.local:
        raise ConfigError("--project-dir requires --scope local")
    if project_dir is not None and target_dir is not None:
        raise ConfigError("--project-dir cannot be combined with --target-dir")
    if target_dir is not None:
        return [
            SkillInstallDestination(
                target=target.value,
                scope=scope,
                root=_target_skill_root(target_dir),
            )
        ]

    project_root = _local_project_root(project_dir) if scope == SkillInstallScope.local else None
    if target == SkillInstallTarget.codex:
        return [_codex_destination(scope, project_root=project_root)]
    if target == SkillInstallTarget.claude:
        return [_claude_destination(scope, project_root=project_root)]
    return [
        _codex_destination(scope, project_root=project_root),
        _claude_destination(scope, project_root=project_root),
    ]


def _target_skill_root(target_dir: Path) -> Path:
    root = target_dir.expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ConfigError(f"target skill directory is not a directory: {root}")
    return root


def _plan_install(
    registry: PluginRegistry,
    selected_names: list[str],
    targets: list[SkillInstallDestination],
    *,
    force: bool,
) -> list[tuple[SkillSpec, SkillInstallDestination, Path]]:
    plan: list[tuple[SkillSpec, SkillInstallDestination, Path]] = []
    for name in selected_names:
        spec = registry.skills[name]
        for target_destination in targets:
            destination = target_destination.root / name
            if destination.exists() and not force:
                raise ConfigError(f"skill already exists: {name}")
            plan.append((spec, target_destination, destination))
    return plan


def _install_skill(
    spec: SkillSpec,
    *,
    target_destination: SkillInstallDestination,
    destination: Path,
    force: bool,
) -> None:
    if destination.exists():
        if not force:
            raise ConfigError(f"skill already exists: {spec.name}")
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        spec.source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    destination.joinpath(".untaped-skill.json").write_text(
        json.dumps(
            {
                "install_root": str(target_destination.root),
                "name": spec.name,
                "scope": target_destination.scope.value,
                "source": str(spec.source),
                "target": target_destination.target,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _codex_destination(
    scope: SkillInstallScope,
    *,
    project_root: Path | None,
) -> SkillInstallDestination:
    if scope == SkillInstallScope.local:
        if project_root is None:
            raise ConfigError("local skill install requires a project directory")
        root = project_root / ".agents" / "skills"
    else:
        root = Path.home() / ".agents" / "skills"
    return SkillInstallDestination(target="codex", scope=scope, root=root)


def _claude_destination(
    scope: SkillInstallScope,
    *,
    project_root: Path | None,
) -> SkillInstallDestination:
    if scope == SkillInstallScope.local:
        if project_root is None:
            raise ConfigError("local skill install requires a project directory")
        root = project_root / ".claude" / "skills"
    else:
        root = Path.home() / ".claude" / "skills"
    return SkillInstallDestination(target="claude", scope=scope, root=root)


def _local_project_root(project_dir: Path | None) -> Path:
    if project_dir is not None:
        return project_dir.expanduser().resolve()
    cwd = Path.cwd().resolve()
    return _git_root(cwd) or cwd


def _git_root(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root).resolve() if root else None


def _render_collection(
    rows: list[dict[str, object]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> str:
    if fmt == "table":
        return ui_context().collection(rows, fmt=fmt, columns=columns)
    return UiContext().collection(rows, fmt=fmt, columns=columns)
