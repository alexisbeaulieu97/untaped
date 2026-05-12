"""Domain entities for the GitHub bounded context."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class GithubUser(BaseModel):
    """Authenticated GitHub user as returned by ``GET /user``."""

    model_config = ConfigDict(extra="ignore")

    login: str
    id: int
    name: str | None = None
    email: str | None = None


class RepoResult(BaseModel):
    """One row of ``GET /search/repositories``."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    full_name: str
    html_url: str
    description: str | None = None
    language: str | None = None
    stargazers_count: int = 0
    forks_count: int = 0
    archived: bool = False
    fork: bool = False
    private: bool = False
    updated_at: str | None = None


class CodeResult(BaseModel):
    """One row of ``GET /search/code``.

    The GitHub payload nests the repository under ``repository`` and the
    file metadata at the top level. We flatten ``repository.full_name``
    into ``repo`` so column selection stays one level deep for the common
    case while leaving the full nested dict accessible via ``repository``.
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    path: str
    sha: str
    html_url: str
    repo: str = ""
    repository: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _flatten_repository(cls, data: Any) -> Any:
        if isinstance(data, dict) and "repo" not in data:
            repository = data.get("repository") or {}
            if isinstance(repository, dict):
                return {**data, "repo": repository.get("full_name", "")}
        return data


class IssueResult(BaseModel):
    """One row of ``GET /search/issues``.

    Covers both issues and pull requests — distinguished by the presence
    of ``pull_request`` in the raw payload, surfaced here as
    ``is_pull_request``.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    number: int
    title: str
    state: str
    html_url: str
    repository_url: str
    user_login: str | None = None
    is_pull_request: bool = False

    @model_validator(mode="before")
    @classmethod
    def _flatten_issue(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        patch: dict[str, Any] = {}
        if "user_login" not in data:
            user = data.get("user") or {}
            if isinstance(user, dict):
                patch["user_login"] = user.get("login")
        if "is_pull_request" not in data:
            patch["is_pull_request"] = "pull_request" in data and data["pull_request"] is not None
        return {**data, **patch} if patch else data


class UserResult(BaseModel):
    """One row of ``GET /search/users``."""

    model_config = ConfigDict(extra="ignore")

    id: int
    login: str
    type: str
    html_url: str
