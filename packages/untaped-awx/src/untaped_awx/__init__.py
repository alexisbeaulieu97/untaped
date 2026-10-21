"""AWX plugin for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import PluginSpec, SkillAsset, online_check
from untaped_awx.settings import RESOURCES, AwxSettings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cyclopts import App

    from untaped.contracts import Contract

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the awx cyclopts app."""
    from untaped_awx.cli import app  # noqa: PLC0415

    return app


def _probe_api() -> str:
    """``doctor --online``: authenticate against AWX (imports the CLI lazily)."""
    from untaped_awx.cli.doctor import probe_api  # noqa: PLC0415

    return probe_api()


def _lint() -> Sequence[Contract]:
    """awx's ``Linter`` and ``TreeReader``: its rules and its documents (imports lazily)."""
    from untaped_awx.providers.lint import AwxDocuments, AwxRules  # noqa: PLC0415

    return (AwxRules(), AwxDocuments())


SPEC = PluginSpec(
    name="awx",
    app_factory=build_app,
    help="Talk to Ansible Automation Platform / AWX.",
    settings=AwxSettings,
    guides=files("untaped_awx") / "guides",
    skills=(
        SkillAsset(
            name="untaped-awx",
            source=Path(str(files("untaped_awx").joinpath("skills", "untaped-awx"))),
            description=(
                "Operates Ansible Automation Platform (AAP) or AWX through the `untaped awx` "
                "command and proves playbook changes with `awx test` suites. Use when the user "
                "mentions AAP, AWX, Tower or automation controller, job or workflow templates, "
                "launching, syncing or following jobs, inventories, projects, schedules, "
                "linting AWX documents or resources, or testing a playbook, role or template "
                "change."
            ),
        ),
    ),
    doctor_checks=(online_check("awx.api", section="awx", probe=_probe_api),),
    caches=(RESOURCES,),
    provides={"lint": _lint},
)
