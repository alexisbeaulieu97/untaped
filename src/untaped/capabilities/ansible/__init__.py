"""Ansible capability for the unified ``untaped`` shell (Wave 2 slice 4).

Exposes a nullary :func:`build_app` factory plus a static ``SPEC:
CapabilitySpec`` (spec §9 gate 1). The lazy ``app`` PEP-562 re-export from
the standalone tool is retired: importing this package never constructs
the CLI tree; :func:`build_app` imports it on demand at mount time.

GitHub behavior is consumed ONLY through the reviewed closed API
:mod:`untaped.capabilities.github.ansible` (Wave 2 prerequisite,
import-plan amendment 1) — never through sibling implementation
modules, and never through the sixteen ``untaped.capability_api``
provider helpers.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.ansible.settings import AnsibleSettings, AnsibleState
from untaped.capability_api import CapabilitySpec, SkillAsset

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the ansible cyclopts app."""
    from untaped.capabilities.ansible.cli import app  # noqa: PLC0415

    return app


SPEC = CapabilitySpec(
    name="ansible",
    app_factory=build_app,
    config_section="ansible",
    profile_model=AnsibleSettings,
    state_model=AnsibleState,
    skills=(
        SkillAsset(
            name="untaped-ansible",
            source=Path(
                str(files("untaped.capabilities.ansible").joinpath("skills", "untaped-ansible"))
            ),
            description="Use the untaped-ansible CLI.",
        ),
    ),
    doctor_checks=(),
)
