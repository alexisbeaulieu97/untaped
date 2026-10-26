"""Settings for the Jira plugin."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from untaped.sdk import Credential, CredentialKey, NeedsSetup, TlsVerify
from untaped_jira.changes import ConfirmPolicy

DEFAULT_ASSIGNED_JQL = "assignee = currentUser() AND resolution = Unresolved"


class JiraServer(CredentialKey):
    """The Jira a token is for: its URL, and how its certificate is checked."""

    url: HttpUrl = Field(
        description="Jira Data Center URL, for example `https://jira.example.com`."
    )
    verify_tls: TlsVerify = Field(
        default=True,
        description=(
            "Check Jira's certificate: `true` (untaped's trust), `false`, "
            "or a CA file for this server alone."
        ),
    )


class JiraSettings(BaseModel):
    """Connection and behavior settings for one Jira Data Center target."""

    model_config = ConfigDict(frozen=True)

    server: Annotated[JiraServer | None, NeedsSetup] = Field(
        default=None,
        description=(
            "The Jira server: its `url` and `verify_tls`. Its personal access token is "
            "stored with `untaped auth login jira`."
        ),
    )
    api_prefix: str = Field(default="/rest/api/2", description="Jira platform REST prefix.")
    agile_prefix: str = Field(
        default="/rest/agile/1.0", description="Jira Software (boards, sprints) REST prefix."
    )
    assigned_jql: str = Field(
        default=DEFAULT_ASSIGNED_JQL,
        description="Base JQL for `issues assigned`, and for `issues search` with no query.",
    )
    default_project: str | None = Field(
        default=None, description="Project key `issues create` uses when `--project` is omitted."
    )
    default_board_id: int | None = Field(
        default=None, description="Board `sprints list` uses when `--board-id` is omitted."
    )
    page_size: int = Field(default=50, gt=0, description="Results requested per Jira API page.")
    confirm: ConfirmPolicy = Field(
        default="destructive",
        description=(
            "Which writes ask first: `always`, `destructive` (patches that replace "
            "or remove values, assignee changes, transitions) or `never`. `--yes` "
            "skips the prompt."
        ),
    )

    @property
    def url(self) -> str | None:
        """The server's URL as text, or ``None`` while it is not set up."""
        return None if self.server is None else str(self.server.url)

    @field_validator("api_prefix", "agile_prefix")
    @classmethod
    def _prefix_shape(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("API prefixes must start with '/'")
        return value.rstrip("/") or "/"

    @field_validator("assigned_jql")
    @classmethod
    def _assigned_jql_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("assigned_jql must not be blank")
        return stripped


#: Jira's personal access token, filed per server by ``untaped auth login jira``;
#: the plugin class holds it as ``token``.
TOKEN = Credential(key=JiraServer, env=("JIRA_API_TOKEN",))
