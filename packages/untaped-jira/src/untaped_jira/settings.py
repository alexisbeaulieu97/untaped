"""Settings for the Jira plugin."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from untaped.sdk import TokenCommand, TokenSources
from untaped_jira.domain.changes import ConfirmPolicy

DEFAULT_ASSIGNED_JQL = "assignee = currentUser() AND resolution = Unresolved"


class JiraSettings(BaseModel):
    """Connection and behavior settings for one Jira Data Center target."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("JIRA_API_TOKEN",))

    model_config = ConfigDict(frozen=True)

    base_url: str | None = Field(
        default=None, description="Jira Data Center URL, for example `https://jira.example.com`."
    )
    token: SecretStr | None = Field(
        default=None,
        description=(
            "Jira personal access token. Falls back to `token_command`, then `JIRA_API_TOKEN`."
        ),
    )
    token_command: TokenCommand = Field(
        default=None,
        description=(
            "Command (argv list, no shell) that prints the token; used when `jira.token` is unset."
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
