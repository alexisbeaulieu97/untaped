"""Orchestration capability for the unified ``untaped`` shell (Wave 2 slice 6).

Exposes a nullary :func:`build_app` factory plus a static ``SPEC:
CapabilitySpec`` (spec §9 gate 1). The lazy ``app`` PEP-562 re-export from
the standalone tool is retired: importing this package never constructs
the CLI tree; :func:`build_app` imports it on demand at mount time.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.orchestration.settings import OrchestrationSettings
from untaped.capability_api import CapabilitySpec, SkillAsset

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the orchestration cyclopts app."""
    from untaped.capabilities.orchestration.cli import app  # noqa: PLC0415

    return app


SPEC = CapabilitySpec(
    name="orchestration",
    app_factory=build_app,
    config_section="orchestration",
    profile_model=OrchestrationSettings,
    skills=(
        SkillAsset(
            name="untaped-orchestration",
            source=Path(
                str(
                    files("untaped.capabilities.orchestration").joinpath(
                        "skills", "untaped-orchestration"
                    )
                )
            ),
            description="Use typed repository orchestration stores with untaped orchestration.",
        ),
    ),
    doctor_checks=(),
)
