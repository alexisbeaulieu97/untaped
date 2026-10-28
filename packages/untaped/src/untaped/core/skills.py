"""Agent skill selection, planning, and installation helpers for the root CLI."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from untaped.core import skill_stubs
from untaped.core.skill_stubs import STUB_MARKER, Comparison, StubAuthor
from untaped.sdk._stdin import read_identifiers
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.projects import checkout_root
from untaped.sdk.storage._fs import atomic_write, is_write_temp


class InstallableSkill(Protocol):
    """A packaged agent skill the install machinery can list and install: a stub.

    Structural type implemented by :class:`~untaped.sdk.SkillAsset` (a frozen
    dataclass). The members are read-only properties because the concrete type
    is frozen; a plain attribute Protocol would (wrongly) demand settability.
    """

    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...


@dataclass(frozen=True)
class PackagedSkill:
    """A skill a composed plugin (or core) ships, and who ships it.

    ``plugin`` is the owning spec's name, which a stub's pages use
    (``untaped docs <plugin>``); ``distribution`` and ``version`` are the
    plugin side a stub marker records (``version`` is empty when unknown;
    core's is read when a marker is written, so composing reads no metadata).
    """

    asset: InstallableSkill
    plugin: str
    distribution: str
    version: str = ""

    @property
    def name(self) -> str:
        """The skill's full ID, also its directory name."""
        return self.asset.name

    @property
    def description(self) -> str:
        """The skill's description, the stub's only plugin-written text."""
        return self.asset.description

    def author(self) -> StubAuthor:
        """The versions this run records in a stub marker (core's plugin side is untaped's)."""
        untaped = skill_stubs.running_untaped()
        return StubAuthor(
            untaped=untaped,
            plugin=self.distribution,
            plugin_version=untaped if self.distribution == skill_stubs.CORE else self.version,
        )

    def stub_files(self) -> dict[str, bytes]:
        """The files this run writes for the stub."""
        return skill_stubs.stub_files(self.name, self.plugin, self.description)


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


@dataclass(frozen=True)
class SkillInstallResult:
    """One committed skill installation."""

    name: str
    target: str
    scope: SkillInstallScope
    root: Path
    destination: Path


def skill_rows(skills: Mapping[str, PackagedSkill]) -> list[dict[str, object]]:
    """Return deterministic row data for registered skills."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
        }
        for spec in sorted(skills.values(), key=lambda item: item.name)
    ]


def install_skills(
    skills: Mapping[str, PackagedSkill],
    skill_names: list[str],
    *,
    stdin: bool,
    all_skills: bool,
    target: SkillInstallTarget,
    force: bool,
    scope: SkillInstallScope,
    project_dir: Path | None,
    target_dir: Path | None,
    on_installed: Callable[[SkillInstallResult], None] | None = None,
) -> list[SkillInstallResult]:
    """Install selected skills after validating the full plan up front."""
    selected_names = _selected_skill_names(
        skills,
        skill_names,
        stdin=stdin,
        all_skills=all_skills,
    )
    targets = _install_targets(
        target,
        scope=scope,
        project_dir=project_dir,
        target_dir=target_dir,
    )
    install_plan = _plan_install(skills, selected_names, targets, force=force)
    results: list[SkillInstallResult] = []
    for asset, target_destination, destination in install_plan:
        result = _install_skill(
            asset,
            target_destination=target_destination,
            destination=destination,
            force=force,
        )
        results.append(result)
        if on_installed is not None:
            on_installed(result)
    return results


class SkillState(StrEnum):
    """How an installed skill compares with the skills this version ships.

    ``outdated`` is a stub this run is newer than (with other text); ``newer``
    is a stub a newer untaped or plugin wrote; ``edited`` is a stub whose
    files someone changed.
    """

    current = "current"
    outdated = "outdated"
    orphaned = "orphaned"
    edited = "edited"
    newer = "newer"


@dataclass(frozen=True)
class InstalledSkill:
    """One untaped-installed stub skill directory found in a skill root.

    ``written_by`` names what wrote a ``newer`` stub (``untaped (11.3.0)``).
    """

    name: str
    target: str
    scope: SkillInstallScope
    path: Path
    state: SkillState
    written_by: str = ""

    @property
    def home(self) -> bool:
        """Whether it sits in a home-folder skill root, decided by path alone."""
        return self.path.parent.resolve() in {root.resolve() for root in home_skill_roots()}


def home_skill_roots() -> frozenset[Path]:
    """The two home-folder skill roots, ``~/.agents/skills`` and ``~/.claude/skills``.

    The per-run check writes only here. Any other root is a project's own,
    often committed, and changes only when someone runs ``skills update``
    there; when a project root is a home root (a project at ``$HOME``), the
    home rule wins.
    """
    return frozenset(
        destination.root
        for destination in (
            _codex_destination(SkillInstallScope.global_, project_root=None),
            _claude_destination(SkillInstallScope.global_, project_root=None),
        )
    )


def skill_roots(*, project_dir: Path | None) -> list[SkillInstallDestination]:
    """Return the global codex/claude skill roots plus ``project_dir``'s local ones."""
    roots = [
        _codex_destination(SkillInstallScope.global_, project_root=None),
        _claude_destination(SkillInstallScope.global_, project_root=None),
    ]
    if project_dir is not None:
        local = SkillInstallScope.local
        roots.append(_codex_destination(local, project_root=project_dir))
        roots.append(_claude_destination(local, project_root=project_dir))
    unique: dict[Path, SkillInstallDestination] = {}
    for root in roots:
        unique.setdefault(root.root, root)
    return list(unique.values())


def find_installed_skills(
    skills: Mapping[str, PackagedSkill],
    roots: Iterable[SkillInstallDestination],
    *,
    deep: bool = True,
) -> list[InstalledSkill]:
    """Return every untaped-installed skill under ``roots`` with its state.

    Only directories carrying a stub marker count; hand-made skills are
    never reported. A marked skill no composed
    plugin ships is ``orphaned``. Without ``deep`` a stub this run is not
    newer than is read by its marker alone and reported ``current``, even
    when someone edited it: the per-run check has nothing to do for it.
    """
    found: list[InstalledSkill] = []
    for root in roots:
        if not root.root.is_dir():
            continue
        for path in sorted(root.root.iterdir()):
            if (path / STUB_MARKER).is_file():
                found.append(_found_stub(skills.get(path.name), path, root, deep=deep))
    return found


def _found_stub(
    spec: PackagedSkill | None, path: Path, root: SkillInstallDestination, *, deep: bool
) -> InstalledSkill:
    marker = skill_stubs.read_marker(path)
    target, scope = root.target, root.scope
    if marker is not None:
        target = marker.target
        scope = _scope(marker.scope, root.scope)
    written_by = ""
    if spec is None:
        state = SkillState.orphaned
    elif marker is None:
        # An unreadable marker can't say who wrote the stub: treat it as the user's.
        state = SkillState.edited
    else:
        run = spec.author()
        comparison = skill_stubs.compare(marker.author, run)
        if comparison is Comparison.older:
            state = SkillState.newer
            written_by = skill_stubs.newer_side(marker.author, run)
        elif comparison is Comparison.same and not deep:
            state = SkillState.current
        else:
            files = _tree_bytes(path)
            if skill_stubs.digest(files) != marker.sha256:
                state = SkillState.edited
            elif comparison is Comparison.newer and files != spec.stub_files():
                state = SkillState.outdated
            else:
                state = SkillState.current
    return InstalledSkill(
        name=path.name,
        target=target,
        scope=scope,
        path=path,
        state=state,
        written_by=written_by,
    )


def outdated_skills(
    skills: Mapping[str, PackagedSkill], *, project_dir: Path | None = None
) -> list[InstalledSkill]:
    """Return installed skills that are not ``current``.

    Looks in the global codex/claude skill roots and, with ``project_dir``,
    that directory's local roots.
    """
    return [
        installed
        for installed in find_installed_skills(skills, skill_roots(project_dir=project_dir))
        if installed.state is not SkillState.current
    ]


def update_installed_skill(
    skills: Mapping[str, PackagedSkill],
    installed: InstalledSkill,
) -> SkillInstallResult:
    """Rewrite ``installed`` in place with what this version ships for the same skill.

    The stub is rewritten file by file, so a parallel run never finds its
    directory missing.
    """
    spec = skills.get(installed.name)
    if spec is None:
        raise ConfigError(
            f"no plugin installed here ships this skill: {installed.name}", category="not_found"
        )
    destination = SkillInstallDestination(
        target=installed.target, scope=installed.scope, root=installed.path.parent
    )
    try:
        _rewrite_stub(spec, destination, installed.path)
    except OSError as exc:
        raise ConfigError(f"could not update {installed.path}: {exc.strerror or exc}") from exc
    return SkillInstallResult(
        name=spec.name,
        target=destination.target,
        scope=destination.scope,
        root=destination.root,
        destination=installed.path,
    )


def remove_installed_skill(installed: InstalledSkill) -> None:
    """Delete one untaped-installed skill directory.

    The directory is moved aside first, so an interrupted removal never leaves
    a half-deleted skill where the agent would still load it.
    """
    if not (installed.path / STUB_MARKER).is_file():
        raise ConfigError(f"not an untaped-installed skill: {installed.path}", category="invalid")
    backup = _backup_path(installed.path)
    try:
        os.replace(installed.path, backup)
    except OSError as exc:
        raise ConfigError(f"could not remove {installed.path}: {exc.strerror or exc}") from exc
    shutil.rmtree(backup, ignore_errors=True)


def _scope(value: str, default: SkillInstallScope) -> SkillInstallScope:
    try:
        return SkillInstallScope(value)
    except ValueError:
        return default


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name != STUB_MARKER
        and not is_write_temp(path.name)  # a stub write in flight
    }


def _selected_skill_names(
    skills: Mapping[str, PackagedSkill],
    skill_names: list[str],
    *,
    stdin: bool,
    all_skills: bool,
) -> list[str]:
    selector_count = int(bool(skill_names)) + int(stdin) + int(all_skills)
    if selector_count != 1:
        raise UsageError("provide skill names, --stdin, or --all; not more than one")
    selected = sorted(skills) if all_skills else read_identifiers(skill_names, stdin=stdin)
    duplicate = _first_duplicate(selected)
    if duplicate is not None:
        raise UsageError(f"duplicate skill selected: {duplicate}")
    missing = [name for name in selected if name not in skills]
    if len(missing) == 1:
        raise ConfigError(f"unknown skill: {missing[0]}", category="not_found")
    if missing:
        raise ConfigError(f"unknown skills: {', '.join(missing)}", category="not_found")
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
        raise UsageError("--target-dir requires --target codex or --target claude")
    if project_dir is not None and scope != SkillInstallScope.local:
        raise UsageError("--project-dir requires --scope local")
    if project_dir is not None and target_dir is not None:
        raise UsageError("--project-dir cannot be combined with --target-dir")
    if target_dir is not None:
        return [
            SkillInstallDestination(
                target=target.value,
                scope=scope,
                root=_target_skill_root(target_dir),
            )
        ]

    project_root = local_project_root(project_dir) if scope == SkillInstallScope.local else None
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
        raise ConfigError(f"target skill directory is not a directory: {root}", category="invalid")
    return root


def _plan_install(
    skills: Mapping[str, PackagedSkill],
    selected_names: list[str],
    targets: list[SkillInstallDestination],
    *,
    force: bool,
) -> list[tuple[PackagedSkill, SkillInstallDestination, Path]]:
    plan: list[tuple[PackagedSkill, SkillInstallDestination, Path]] = []
    for name in selected_names:
        spec = skills[name]
        for target_destination in targets:
            destination = target_destination.root / name
            if destination.exists() and not force:
                raise ConfigError(f"skill already exists: {name}", category="conflict")
            plan.append((spec, target_destination, destination))
    return plan


def _install_skill(
    spec: PackagedSkill,
    *,
    target_destination: SkillInstallDestination,
    destination: Path,
    force: bool,
) -> SkillInstallResult:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage beside the final destination, then place by rename. Forced
    # replacements move the old install aside first so an interrupted replace
    # cannot leave a half-deleted destination.
    stage = Path(tempfile.mkdtemp(dir=destination.parent, prefix=f".{destination.name}.tmp-"))
    try:
        staged_skill = _stage_skill(spec, target_destination, stage / destination.name)
        _place_staged_skill(staged_skill, destination, force=force)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return SkillInstallResult(
        name=spec.name,
        target=target_destination.target,
        scope=target_destination.scope,
        root=target_destination.root,
        destination=destination,
    )


def _stage_skill(
    spec: PackagedSkill, target_destination: SkillInstallDestination, staged_skill: Path
) -> Path:
    staged_skill.mkdir()
    files = spec.stub_files()
    for relative, content in files.items():
        staged_skill.joinpath(relative).write_bytes(content)
    _write_stub_marker(spec, target_destination, staged_skill, files)
    return staged_skill


def _write_stub_marker(
    spec: PackagedSkill,
    target_destination: SkillInstallDestination,
    directory: Path,
    files: dict[str, bytes],
) -> None:
    author = spec.author()
    skill_stubs.write_marker(
        directory,
        plugin=author.plugin,
        plugin_version=author.plugin_version,
        sha256=skill_stubs.digest(files),
        name=spec.name,
        target=target_destination.target,
        scope=target_destination.scope.value,
        install_root=target_destination.root,
    )


def _rewrite_stub(
    spec: PackagedSkill, target_destination: SkillInstallDestination, destination: Path
) -> None:
    """Rewrite a stub's files in place, each by temporary file and rename.

    The directory never goes missing, so parallel runs refreshing one stub
    both succeed. Files the new stub no longer has are removed.
    """
    files = spec.stub_files()
    keep = {*files, STUB_MARKER}
    for relative, content in files.items():
        atomic_write(destination / relative, content)
    for stale in destination.iterdir():
        # A parallel run's temporary file is its own to rename or remove.
        if stale.name not in keep and not is_write_temp(stale.name):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
            else:
                stale.unlink(missing_ok=True)
    _write_stub_marker(spec, target_destination, destination, files)


def _place_staged_skill(staged_skill: Path, destination: Path, *, force: bool) -> None:
    if not destination.exists():
        os.replace(staged_skill, destination)
        return
    # Existing destinations only reach placement after _plan_install accepts
    # force=True; non-force conflicts are a preflight ConfigError.
    assert force
    backup = _backup_path(destination)
    os.replace(destination, backup)
    try:
        os.replace(staged_skill, destination)
    except BaseException as placement_error:
        # Catch BaseException so KeyboardInterrupt/SystemExit during the swap
        # still attempt to restore the old install before being re-raised.
        if not destination.exists() and backup.exists():
            try:
                os.replace(backup, destination)
            except BaseException as restore_error:
                raise placement_error from restore_error
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _backup_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.backup-{uuid4().hex}"


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


def local_project_root(project_dir: Path | None) -> Path:
    """``project_dir``, else the working directory's :func:`checkout_root`, else itself.

    Where ``--scope local`` skills live, for installing and for checking them.
    """
    if project_dir is not None:
        return project_dir.expanduser().resolve()
    cwd = Path.cwd().resolve()
    return checkout_root(cwd) or cwd
