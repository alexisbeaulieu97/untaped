"""Settings for the Jira tool."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

DEFAULT_ASSIGNED_JQL = "assignee = currentUser() AND resolution = Unresolved"


class JiraSettings(BaseModel):
    """Connection and behavior settings for one Jira Data Center target."""

    model_config = ConfigDict(frozen=True)

    base_url: str | None = None
    token: SecretStr | None = None
    api_prefix: str = "/rest/api/2"
    agile_prefix: str = "/rest/agile/1.0"
    assigned_jql: str = DEFAULT_ASSIGNED_JQL
    default_project: str | None = None
    default_board_id: int | None = None
    page_size: int = Field(default=50, gt=0)

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
