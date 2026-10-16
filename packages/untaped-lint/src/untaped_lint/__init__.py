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
    from untaped_lint.application.rules import Linter, TreeReader  # noqa: PLC0415

    return (Linter, TreeReader)


SPEC = PluginSpec(
    name="lint",
    app_factory=build_app,
    help="Check plugins, repositories and records against rules, with ids to ignore.",
    stability=experimental,
    settings=LintSettings,
    contracts=_contracts,
    doctor_checks=(DoctorCheck(id=IGNORE_IDS, title="lint.ignore ids", run=ignore_ids_check),),
    skills=(
        SkillAsset(
            name="untaped-lint",
            source=Path(str(files("untaped_lint").joinpath("skills", "untaped-lint"))),
            description=(
                "Runs a repository's conventions as rules through `untaped lint` (its "
                "`.untaped/lint.yml` rules, each plugin's, and the plugin checks), lists them "
                "with `untaped lint rules`, and turns a convention written in prose into a rule "
                "with `untaped lint new`. Use before changing code in a repository that has "
                "`.untaped/lint.yml` or untaped plugins, when `untaped lint` fails, or when the "
                "user explains a convention to keep."
            ),
        ),
    ),
)
