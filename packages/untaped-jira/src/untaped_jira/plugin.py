"""The ``jira`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`Jira`, in this leaf module:
the class imports every command, so anything else under the package imports
without them.
"""

from __future__ import annotations

from untaped.sdk import OnlineCheck, Plugin, StubSkill
from untaped_jira.commands.boards import Boards
from untaped_jira.commands.issues import Issues
from untaped_jira.commands.projects import Projects
from untaped_jira.commands.sprints import Sprints
from untaped_jira.commands.whoami import Whoami, probe
from untaped_jira.settings import TOKEN, JiraSettings


class Jira(Plugin):
    """Manage Jira Data Center and Server issues; not Jira Cloud."""

    settings = JiraSettings
    commands = (Whoami, Issues, Projects, Boards, Sprints)
    token = TOKEN
    api = OnlineCheck(probe=probe)
    skill = StubSkill(
        "Jira Data Center and Server issues (not Jira Cloud): JQL searches, "
        "issue fields, assignees, comments, links and workflow transitions, plus "
        "projects, boards and sprints. Use when the work concerns a Jira issue or an "
        "issue key such as OPS-123, JQL, a sprint or board, or moving an issue to "
        "another status."
    )


__all__ = ["Jira"]
