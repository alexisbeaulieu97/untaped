"""``untaped jira issues links``: link issues to each other."""

from __future__ import annotations

from pydantic import Field

from untaped.sdk import Group, Positional, q
from untaped_jira.client import JiraClient
from untaped_jira.commands._jira import ISSUE_KEY, SendsDirectly
from untaped_jira.keys import validate_issue_key
from untaped_jira.parse import browse_url
from untaped_jira.payloads import build_link_payload
from untaped_jira.records import IssueOutcome


class Create(SendsDirectly):
    """Link KEY to OTHER, read as "KEY OUTWARD-PHRASE OTHER" (OPS-1 Blocks OPS-2).

    With ``Blocks``, KEY blocks OTHER. The preview prints a ``reads as:``
    line in that order. A reversed link is removed in Jira itself; untaped
    has no command that deletes links.
    """

    key: Positional[str] = Field(description=ISSUE_KEY)
    link_type: Positional[str] = Field(description="Link type name (e.g. Blocks, Relates).")
    other: Positional[str] = Field(
        description="Issue key (PROJECT-123, in any case) or numeric id the first issue links to."
    )

    def run(self) -> IssueOutcome:
        key, other = validate_issue_key(self.key), validate_issue_key(self.other)
        settings, link_type = self.settings, self.link_type
        # The REST field names read backwards; say the direction in words.
        reads_as = f"reads as: {key} <outward phrase of {q(link_type)}> {other}"
        outcome = {"key": key, "url": browse_url(settings.url, key), "link_type": link_type}

        def send(client: JiraClient) -> IssueOutcome:
            client.create_link(build_link_payload(key, link_type, other))
            return IssueOutcome(action="linked", linked_key=other, **outcome)

        return self.send(
            [("POST", f"{settings.api_prefix}/issueLink", [reads_as])],
            send,
            planned=IssueOutcome(action="planned", linked_key=other, **outcome),
            progress="Linking issues",
        )


class Links(Group):
    """Link issues to each other."""

    commands = (Create,)
