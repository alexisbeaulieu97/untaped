"""Lint plugin: rules over records, with one id, severity and ignore spelling for all.

Exposes a static ``SPEC: PluginSpec`` plus a nullary :func:`build_app`
factory. Importing this package never constructs the CLI tree.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import DoctorCheck, PluginSpec, SkillAsset, experimental
from untaped_lint.doctor import IGNORE_IDS, ignore_ids_check
from untaped_lint.settings import LintSettings

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.contracts import Contract

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the lint cyclopts app."""
    from untaped_lint.cli import app  # noqa: PLC0415

    return app


def _contracts() -> tuple[type[Contract], ...]:
    from untaped_lint.application.rules import Linter  # noqa: PLC0415

    return (Linter,)


SPEC = PluginSpec(
    name="lint",
    app_factory=build_app,
    help="Check plugins and records against rules, with ids to ignore.",
    stability=experimental,
    settings=LintSettings,
    contracts=_contracts,
    doctor_checks=(DoctorCheck(id=IGNORE_IDS, title="lint.ignore ids", run=ignore_ids_check),),
    skills=(
        SkillAsset(
            name="untaped-lint",
            source=Path(str(files("untaped_lint").joinpath("skills", "untaped-lint"))),
            description=(
                "Checks an untaped plugin against the house conventions and its contract "
                "fills through `untaped lint`, lists the rules with `untaped lint rules`, and "
                "reads or suppresses findings by rule id. Use when the user writes or reviews "
                "an untaped plugin, asks why `untaped lint` failed, or wants a rule ignored."
            ),
        ),
    ),
)
