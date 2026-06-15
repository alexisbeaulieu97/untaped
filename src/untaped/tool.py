"""The untaped SDK tool composition contract.

A standalone tool built on the untaped SDK declares a :class:`ToolSpec` and runs
``run_tool(app, spec)``. ``ToolSpec`` is the SDK-only replacement for the old
``PluginManifest``: it carries everything the SDK needs to wire a tool's config,
profile, and skills surfaces and to render command-aware messages. ``SkillAsset``
replaces the registry ``SkillSpec`` for standalone tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from untaped.errors import ConfigError


@dataclass(frozen=True)
class SkillAsset:
    """A packaged agent skill shipped by a tool."""

    name: str
    source: Path
    description: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigError("skill asset name must not be empty")
        if not self.description.strip():
            raise ConfigError(f"skill asset {self.name!r} must have a description")


@dataclass(frozen=True)
class ToolSpec:
    """Everything the SDK needs to run a standalone tool, as data.

    ``command`` is the tool's executable name (e.g. ``untaped-github``), used for
    command-aware help and error text. ``section`` is the tool's config section.
    ``profile_model`` is its profile-scoped settings model; ``state_model`` (when
    present) is its disjoint, tool-managed state model. ``skills`` are the tool's
    packaged agent skills.
    """

    command: str
    section: str
    profile_model: type[BaseModel]
    state_model: type[BaseModel] | None = None
    skills: Sequence[SkillAsset] = ()

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ConfigError("tool command must not be empty")
        if not self.section.strip():
            raise ConfigError("tool section must not be empty")
        _require_model(self.profile_model, f"tool {self.command!r} profile_model")
        if self.state_model is not None:
            _require_model(self.state_model, f"tool {self.command!r} state_model")
        skills = tuple(self.skills)
        names = [skill.name for skill in skills]
        if len(set(names)) != len(names):
            raise ConfigError(f"tool {self.command!r} has duplicate skill names")
        object.__setattr__(self, "skills", skills)


def _require_model(model: object, label: str) -> None:
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ConfigError(f"{label} must be a pydantic BaseModel subclass")
