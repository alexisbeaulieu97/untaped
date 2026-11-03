"""The port jira's commands talk to a Jira through, in jira's own records.

:class:`JiraApi` is what commands may ask of any Jira; an adapter per API
version (:mod:`untaped_jira.data_center`) serves it, and commands reach it
through ``JiraCommand.client()``, never knowing which version answers.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

from untaped_jira.records import Transition

if TYPE_CHECKING:
    from untaped_jira.records import Board, Comment, Issue, Project, Sprint, User


class Created(BaseModel):
    """What Jira says of an issue it just created."""

    key: str | None = None
    id: str | None = None
    api_url: str | None = None


class Current(BaseModel):
    """An issue's current values, as a preview compares them: by Jira field id.

    ``fields`` holds each field asked for, in the shape Jira sends it (the
    shape a fields document sets it in); ``transitions`` what the issue
    offers from its status, when asked for.
    """

    fields: dict[str, Any] = Field(default_factory=dict)
    transitions: list[Transition] = Field(default_factory=list)


class JiraApi(Protocol):
    """What jira's commands ask of a Jira, in jira's own records.

    A fields document (``create``, ``edit``) is the user's, written in
    Jira's field ids, so it stays a mapping.
    """

    def me(self) -> User:
        """The user the token authenticates as."""
        ...

    def issue(self, key: str) -> Issue:
        """One issue, with the detail fields ``issues get`` shows."""
        ...

    def current(self, key: str, fields: Sequence[str], *, transitions: bool = False) -> Current:
        """The values ``fields`` hold on issue ``key`` now, and its transitions when asked."""
        ...

    def search(self, jql: str, *, limit: int | None) -> Iterator[Issue]:
        """The issues ``jql`` finds, as search rows."""
        ...

    def comments(self, key: str, *, limit: int | None = None) -> Iterator[Comment]:
        """Issue ``key``'s comments, oldest first."""
        ...

    def create(self, document: Mapping[str, Any]) -> Created:
        """Create an issue from a fields document."""
        ...

    def edit(self, key: str, document: Mapping[str, Any]) -> None:
        """Apply a fields document to issue ``key``."""
        ...

    def assign(self, key: str, username: str | None) -> None:
        """Assign issue ``key`` to ``username``; ``None`` unassigns it."""
        ...

    def comment(self, key: str, body: str) -> Comment:
        """Add a comment to issue ``key``."""
        ...

    def link(self, key: str, link_type: str, other: str) -> None:
        """Link ``key`` to ``other``, read as "KEY <link_type's outward phrase> OTHER"."""
        ...

    def transitions(self, key: str) -> list[Transition]:
        """The transitions issue ``key`` offers from its status."""
        ...

    def transition(
        self, key: str, transition_id: str, *, comment: str | None, resolution: str | None
    ) -> None:
        """Apply a transition, with a comment and a resolution in the same request."""
        ...

    def projects(self) -> Iterator[Project]:
        """Every project the user sees."""
        ...

    def project(self, key: str) -> Project:
        """One project."""
        ...

    def boards(
        self,
        *,
        project: str | None,
        name: str | None,
        board_type: str | None,
        limit: int | None,
    ) -> Iterator[Board]:
        """The boards the user sees, filtered."""
        ...

    def sprints(self, board_id: int, *, state: str | None, limit: int | None) -> Iterator[Sprint]:
        """A board's sprints."""
        ...


__all__ = ["Created", "Current", "JiraApi"]
