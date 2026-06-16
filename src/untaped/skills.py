"""Agent skill install helpers shared by the per-tool ``skills`` command group.

:mod:`untaped.skills_app` builds each tool's ``skills list / install`` group on
top of the selection, planning, and copy machinery defined here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Protocol

from cyclopts import Parameter

from untaped.cli import existing_directory
from untaped.errors import ConfigError
from untaped.stdin import read_identifiers


class InstallableSkill(Protocol):
    """A packaged agent skill the install machinery can list and copy.

    Structural type implemented by :class:`~untaped.tool.SkillAsset` (a frozen
    dataclass). The members are read-only properties because the concrete type
    is frozen; a plain attribute Protocol would (wrongly) demand settability.
    """

    @property
    def name(self) -> str: ...
    @property
    def source(self) -> Path: ...
    @property
    def description(self) -> str: ...


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


def skill_rows(skills: Mapping[str, InstallableSkill]) -> list[dict[str, object]]:
    """Return deterministic row data for registered skills."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "source": str(spec.source),
        }
        for spec in sorted(skills.values(), key=lambda item: item.name)
    ]


def _selected_skill_names(
    skills: Mapping[str, InstallableSkill],
    skill_names: list[str],
    *,
    stdin: bool,
    all_skills: bool,
) -> list[str]:
    selector_count = int(bool(skill_names)) + int(stdin) + int(all_skills)
    if selector_count != 1:
        raise ConfigError("provide skill names, --stdin, or --all; not more than one")
    selected = sorted(skills) if all_skills else read_identifiers(skill_names, stdin=stdin)
    duplicate = _first_duplicate(selected)
    if duplicate is not None:
        raise ConfigError(f"duplicate skill selected: {duplicate}")
    missing = [name for name in selected if name not in skills]
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
    skills: Mapping[str, InstallableSkill],
    selected_names: list[str],
    targets: list[SkillInstallDestination],
    *,
    force: bool,
) -> list[tuple[InstallableSkill, SkillInstallDestination, Path]]:
    plan: list[tuple[InstallableSkill, SkillInstallDestination, Path]] = []
    for name in selected_names:
        spec = skills[name]
        for target_destination in targets:
            destination = target_destination.root / name
            if destination.exists() and not force:
                raise ConfigError(f"skill already exists: {name}")
            plan.append((spec, target_destination, destination))
    return plan


def _install_skill(
    spec: InstallableSkill,
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
