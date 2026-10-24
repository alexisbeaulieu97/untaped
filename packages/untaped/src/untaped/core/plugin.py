"""Core as a plugin of itself: ``Core``, the class holding what untaped has for its own use.

Core composes it first, with no entry point (one would make core's own class
removable), and mounts its commands at the root: ``PluginGroup`` is
``untaped plugin``. The root commands not yet moved onto classes still
mount through the 11.x seam (:data:`untaped.runtime.bootstrap.SHELL_SPEC`).
Importing this module imports every command of core's, so a start reads
:data:`~untaped.core.settings.DECLARATION` instead.
"""

from __future__ import annotations

from untaped.core.commands.auth import Auth
from untaped.core.commands.config import Config
from untaped.core.commands.plugins import PluginGroup
from untaped.core.commands.profile import ProfileGroup
from untaped.core.commands.project import ProjectGroup
from untaped.core.commands.skills import Skills
from untaped.core.settings import DECLARATION, NAME, CoreSettings
from untaped.sdk.plugin import Plugin, StubSkill


class Core(Plugin):
    """untaped's own commands: config, profiles, projects, skills, doctor, setup, auth, plugins."""

    settings = CoreSettings
    commands = (Config, ProfileGroup, Auth, ProjectGroup, Skills, PluginGroup)
    declaration = DECLARATION
    skill = StubSkill(
        "Installs, sets up and diagnoses the `untaped` CLI itself (profiles, service "
        "URLs and tokens, `setup plan`, `doctor`, output formats and installed agent "
        "skills). Use when the user asks to install or set up untaped, configure or "
        "switch a profile, connect AWX/AAP, GitHub or Jira, fix a failing "
        "`untaped doctor`, or install untaped's skills for an agent."
    )


__all__ = ["NAME", "Core"]
