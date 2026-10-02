"""Jira capability for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.jira.settings import JiraSettings
from untaped.sdk import CapabilitySpec, SkillAsset, connection_check, online_check

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app", "provider"]


def build_app() -> App:
    """Nullary factory returning the jira cyclopts app."""
    from untaped.capabilities.jira.cli import app  # noqa: PLC0415

    return app


def _probe_api() -> str:
    """``doctor --online``: authenticate against Jira (imports the CLI lazily)."""
    from untaped.capabilities.jira.cli.doctor import probe_api  # noqa: PLC0415

    return probe_api()


SPEC = CapabilitySpec(
    name="jira",
    app_factory=build_app,
    help="Manage Jira Data Center issues from untaped.",
    config_section="jira",
    profile_model=JiraSettings,
    skills=(
        SkillAsset(
            name="untaped-jira",
            source=Path(str(files("untaped.capabilities.jira").joinpath("skills", "untaped-jira"))),
            description=(
                "Works with Jira Data Center or Server issues through the `untaped jira` command "
                "(JQL search, reading, creating, editing, assigning, commenting on, linking and "
                "transitioning issues, and listing projects, boards and sprints). Use when the "
                "user mentions Jira, an issue key such as OPS-123, JQL, a sprint or board, or "
                "moving an issue to another status."
            ),
        ),
    ),
    doctor_checks=(
        connection_check("jira.connection", section="jira"),
        online_check("jira.api", section="jira", probe=_probe_api),
    ),
)


def provider() -> CapabilitySpec:
    """Entry-point provider: the ``untaped.capabilities`` entry point names this."""
    return SPEC
