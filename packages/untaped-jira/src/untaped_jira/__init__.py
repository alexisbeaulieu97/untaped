"""Jira plugin for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from untaped.sdk import PluginSpec, SkillAsset, online_check
from untaped_jira.settings import JiraSettings

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the jira cyclopts app."""
    from untaped_jira.cli import app  # noqa: PLC0415

    return app


def _probe_api() -> str:
    """``doctor --online``: authenticate against Jira (imports the CLI lazily)."""
    from untaped_jira.cli.doctor import probe_api  # noqa: PLC0415

    return probe_api()


SPEC = PluginSpec(
    name="jira",
    app_factory=build_app,
    help="Manage Jira Data Center and Server issues; not Jira Cloud.",
    settings=JiraSettings,
    guides=files("untaped_jira") / "guides",
    skills=(
        SkillAsset(
            name="untaped-jira",
            description=(
                "Jira Data Center and Server issues (not Jira Cloud): JQL searches, "
                "issue fields, assignees, comments, links and workflow transitions, plus "
                "projects, boards and sprints. Use when the work concerns a Jira issue or an "
                "issue key such as OPS-123, JQL, a sprint or board, or moving an issue to "
                "another status."
            ),
        ),
    ),
    doctor_checks=(online_check("jira.api", section="jira", probe=_probe_api),),
)
