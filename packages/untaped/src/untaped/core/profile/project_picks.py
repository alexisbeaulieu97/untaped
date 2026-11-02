"""The projects whose ``profile:`` picks a profile, which ``profile rename`` and ``delete`` follow.

A project picks the home's profiles by name in its ``.untaped/config.yml``
(:mod:`untaped.runtime.folder_layer`), so renaming or deleting a profile
rewrites those names: a rename gives them the new name, a delete drops the
name (and ``profile:`` when nothing is left), through
:mod:`untaped.core.profile.repository`. The projects come from the
home's records (:func:`untaped.sdk.projects.known_projects`); a project
whose folder is away, or whose file can't be read, is not one this finds.
A file the layer would ignore (someone else's, or writable by others) is
listed and left as it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from untaped.runtime.folder_layer import project_config_path, project_picks, untrusted
from untaped.sdk.errors import ConfigError
from untaped.sdk.projects import known_projects
from untaped.sdk.storage.holds import HolderView
from untaped.sdk.words import shown_path


@dataclass(frozen=True)
class ProjectPick:
    """A project whose ``profile:`` names the profile, and why it can't be rewritten, if so."""

    name: str
    file: Path
    problem: str | None = None

    @property
    def line(self) -> str:
        """``PROJ-1 (~/work/PROJ-1/.untaped/config.yml)``, with the problem when there is one."""
        shown = f"{self.name} ({shown_path(self.file)})"
        return shown if self.problem is None else f"{shown}: left as it is, {self.problem}"


def projects_picking(profile: str) -> list[ProjectPick]:
    """Every present project whose ``profile:`` names ``profile``, by name."""
    found: list[ProjectPick] = []
    for seen in known_projects():
        if not isinstance(seen, HolderView) or seen.state != "present":
            continue
        file = project_config_path(seen.anchor)
        if not file.is_file():
            continue
        try:
            picks = project_picks(file)
        except ConfigError:
            continue  # reading the project reports its own error
        if profile in picks:
            found.append(ProjectPick(seen.anchor.name, file, untrusted(file)))
    return sorted(found, key=lambda each: (each.name, str(each.file)))


__all__ = ["ProjectPick", "projects_picking"]
