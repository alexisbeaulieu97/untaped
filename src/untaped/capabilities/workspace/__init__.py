"""Workspace capability for the unified ``untaped`` shell (Wave 1.5).

Exposes a nullary :func:`build_app` factory plus a static ``SPEC:
CapabilitySpec`` (spec §9 gate 1). The lazy ``app`` PEP-562 re-export from
the standalone tool is retired: importing this package never constructs
the CLI tree; :func:`build_app` imports it on demand at mount time.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.registry import CapabilitySpec, SkillAsset
from untaped.capabilities.workspace.settings import WorkspaceSettings, WorkspaceState

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the workspace cyclopts app."""
    from untaped.capabilities.workspace.cli import app  # noqa: PLC0415

    return app


SPEC = CapabilitySpec(
    name="workspace",
    app_factory=build_app,
    config_section="workspace",
    profile_model=WorkspaceSettings,
    state_model=WorkspaceState,
    skills=(
        SkillAsset(
            name="untaped-workspace",
            source=Path(
                str(files("untaped.capabilities.workspace").joinpath("skills", "untaped-workspace"))
            ),
            description="Use the untaped-workspace CLI.",
        ),
    ),
    doctor_checks=(),
)
