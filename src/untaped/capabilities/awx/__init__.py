"""AWX capability for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.awx.settings import AwxSettings
from untaped.sdk import CapabilitySpec, SkillAsset, connection_check, online_check

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app", "provider"]


def build_app() -> App:
    """Nullary factory returning the awx cyclopts app."""
    from untaped.capabilities.awx.cli import app  # noqa: PLC0415

    return app


def _probe_api() -> str:
    """``doctor --online``: authenticate against AWX (imports the CLI lazily)."""
    from untaped.capabilities.awx.cli.doctor import probe_api  # noqa: PLC0415

    return probe_api()


SPEC = CapabilitySpec(
    name="awx",
    app_factory=build_app,
    help="Talk to Ansible Automation Platform / AWX.",
    config_section="awx",
    profile_model=AwxSettings,
    skills=(
        SkillAsset(
            name="untaped-awx",
            source=Path(str(files("untaped.capabilities.awx").joinpath("skills", "untaped-awx"))),
            description=(
                "Operates Ansible Automation Platform (AAP) or AWX through the `untaped awx` "
                "command and proves playbook changes with `awx test` suites. Use when the user "
                "mentions AAP, AWX, Tower or automation controller, job or workflow templates, "
                "launching, syncing or following jobs, inventories, projects, schedules, or "
                "testing a playbook, role or template change."
            ),
        ),
    ),
    doctor_checks=(
        connection_check("awx.connection", section="awx"),
        online_check("awx.api", section="awx", probe=_probe_api),
    ),
)


def provider() -> CapabilitySpec:
    """Entry-point provider: the ``untaped.capabilities`` entry point names this."""
    return SPEC
