"""The records github's commands write; each one's kind is ``github.`` and its class name.

The module holds models and their default columns only, to keep its import
cheap. A model's fields, in order, are its rows' keys on the wire.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator

from untaped.sdk import (
    AbsolutePath,
    Age,
    Bytes,
    Key,
    ObjectName,
    OutcomeRecord,
    Record,
    Spread,
    Status,
    TargetRecord,
    UtcTimestamp,
)


def _with_aliases(data: Any) -> Any:
    """Derive ``url`` from ``html_url``.

    ``url`` always mirrors ``html_url``: the raw GitHub payload's own ``url``
    is the API link, which must not leak into the web-URL field. A record
    without ``html_url`` (one this model already emitted) keeps its ``url``.
    """
    if not isinstance(data, dict) or "html_url" not in data:
        return data
    return {**data, "url": data["html_url"]}


def _repo_from_repository_url(value: Any) -> str | None:
    """Extract ``owner/name`` from GitHub's repository API URL."""
    if not isinstance(value, str):
        return None
    marker = "/repos/"
    if marker not in value:
        return None
    return value.rsplit(marker, 1)[1].strip("/") or None


class User(Record):
    """The authenticated GitHub user, as ``GET /user`` returns it (``whoami``)."""

    model_config = ConfigDict(extra="ignore")

    login: str
    id: int
    name: str | None = None
    email: str | None = None


class Repo(Record):
    """A GitHub repository, as every github command emits and reads it.

    The fields are GitHub's own (``full_name``, ``html_url``, ``clone_url``…),
    so a raw REST row validates as it is and unknown fields are dropped; only
    ``full_name`` is required, and a field the row's source didn't report is
    ``None``: a cache row lacks most of GitHub's metadata. github fills
    workspace's ``RepoSource`` with it, so a piped row becomes a workspace repo
    without an API call.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)
    table_columns: ClassVar[tuple[str, ...]] = (
        "full_name",
        "default_branch",
        "private",
        "pushed_at",
        "description",
    )

    full_name: Annotated[str, Key]
    name: str | None = Field(default=None, validate_default=True)
    html_url: str | None = None
    clone_url: str | None = None
    ssh_url: str | None = None
    default_branch: str | None = None
    description: str | None = None
    language: str | None = None
    stargazers_count: int | None = None
    forks_count: int | None = None
    private: bool | None = None
    archived: bool = False
    fork: bool | None = None
    # GitHub's last push, in GitHub's own ``…Z`` form: piped into sweep or
    # cache sync it matches the stored value and skips an unchanged repo.
    pushed_at: str | None = None
    updated_at: str | None = None

    @field_validator("name")
    @classmethod
    def _short_name(cls, name: str | None, info: ValidationInfo) -> str | None:
        # An "after" field validator, not a "before" model one: that would
        # hand the rest of a JSON row to strict validation as Python values.
        full_name = info.data.get("full_name")
        if not name and isinstance(full_name, str):
            return full_name.rpartition("/")[2]
        return name


#: The columns ``search repos`` shows by default.
REPO_HIT_COLUMNS = ("full_name", "description", "language", "stargazers_count", "updated_at")


class CorpusRepo(Repo):
    """One repository row in the local scan corpus.

    A :class:`Repo`, so it pipes into workspace like any repo row.
    """

    table_columns: ClassVar[tuple[str, ...]] = (
        "full_name",
        "ref",
        "status",
        "archived",
        "disk_bytes",
        "fetched_at",
        "path",
    )

    ref: str
    path: str = Field(
        description="The repo in the git plugin's repo store, which other plugins may share."
    )
    status: Annotated[
        Literal["synced", "cached", "released", "removed"],
        Status(success=("synced", "removed")),
    ] = Field(
        default="cached",
        description=(
            "`cached` for a stored copy, `synced` for one just fetched; on a `cache delete` or "
            "`prune` row, `removed` when nothing else used the repo, `released` when github's "
            "refs, metadata and worktrees went but the repo stays."
        ),
    )
    kept: str | None = Field(
        default=None,
        description=(
            "What still holds a `released` repo: other plugins, worktrees untaped didn't "
            "add, local branches or a stash."
        ),
    )
    fetched_at: Annotated[UtcTimestamp | None, Age] = None
    profile: str = "default"
    ref_globs: tuple[str, ...] = ()
    detail: str | None = Field(
        default=None,
        description=(
            "On a `cache delete` or `prune` row: what a `removed` repo freed, or what "
            "kept a `released` one."
        ),
    )
    disk_bytes: Annotated[int, Bytes] = Field(
        default=0,
        description="The stored copy's size; on a `cache delete` row, what a `removed` repo freed.",
    )


class SweepRepo(Repo):
    """Aggregated sweep result for one repository.

    A :class:`Repo`, so a sweep's matches pipe into workspace (or a
    second sweep) like any repo row.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("full_name", "hits", "refs_matched", "owners")

    matched: bool = Field(
        default=True, description="Always true: a sweep writes a row only for a repo that matched."
    )
    refs_matched: tuple[str, ...] = Field(
        default=(), description="The refs the predicates held on, by short name."
    )
    hits: Annotated[dict[str, int], Spread] = Field(
        default_factory=dict,
        description=(
            "Per predicate label (`grep:PAT`, `not-grep:PAT`, `has-file:GLOB`, "
            "`lacks-file:GLOB`): matching lines for a `grep`, else 1 when the pattern or "
            "file was found and 0 when not; the most over the matched refs."
        ),
    )
    owners: tuple[str, ...] = Field(
        default=(),
        description=(
            "Owners of the matching files from the default branch's CODEOWNERS (its "
            "default owners when no file matched); empty without one or under `--no-owners`."
        ),
    )
    fetched_at: Annotated[UtcTimestamp | None, Age] = Field(
        default=None, description="When the scanned copy was fetched."
    )


class SweepFile(Record):
    """One file a sweep matched in one repository (``sweep files``).

    ``refs`` are the refs it matched on; ``hits`` counts its matching lines.
    """

    repo: str
    path: str
    refs: tuple[str, ...]
    hits: int


class SweepMatch(Record):
    """One deduped content match, possibly reachable from several refs (``sweep matches``)."""

    # A match line reads whole: the table shows every field.
    table_columns: ClassVar[tuple[str, ...]] = ("repo", "refs", "path", "line", "text")

    repo: str
    refs: tuple[str, ...] = Field(description="Every scanned ref whose file has this line.")
    path: str
    line: int
    text: str


class SyncOutcome(OutcomeRecord):
    """The result of warming one repository in the corpus (``cache sync``).

    ``action`` is ``synced`` (fetched), ``unchanged`` (GitHub reports no push
    since the cached fetch, so none ran), ``skipped`` (the cached copy is
    younger than ``github.caches.sweep.max_age_seconds``), or ``failed``. A failed
    row says why in ``detail`` and carries the structured ``error``.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "detail")

    repo: str
    action: Annotated[str, Status(success=("synced",))]
    fetched_at: UtcTimestamp | None = None
    detail: str | None = None


class Worktree(TargetRecord):
    """A materialized worktree path for a cached repository ref (``cache worktree``).

    ``target_path`` leads so ``--format raw`` prints it, for ``$(…)`` capture,
    and so ``--format pipe`` consumers find the directory. ``path`` holds the
    same directory; it predates ``target_path`` and stays for scripts that read it.
    """

    target_path: AbsolutePath
    path: str
    repo: str
    ref: str


class Code(Record):
    """One row of ``GET /search/code``.

    Flattens ``repository.full_name`` into ``repo`` so column selection
    stays one level deep.
    """

    model_config = ConfigDict(extra="ignore")
    table_columns: ClassVar[tuple[str, ...]] = ("repo", "path")

    name: str
    path: str
    sha: Annotated[str, ObjectName]
    repo: str = ""
    url: str

    @model_validator(mode="before")
    @classmethod
    def _url(cls, data: Any) -> Any:
        return _with_aliases(data)

    @model_validator(mode="before")
    @classmethod
    def _flatten_repository(cls, data: Any) -> Any:
        if isinstance(data, dict) and "repo" not in data:
            repository = data.get("repository") or {}
            if isinstance(repository, dict):
                return {**data, "repo": repository.get("full_name", "")}
        return data


class Issue(Record):
    """One row of ``GET /search/issues``.

    Covers both issues and pull requests — distinguished by the presence
    of ``pull_request`` in the raw payload, surfaced here as
    ``is_pull_request``.
    """

    model_config = ConfigDict(extra="ignore")
    table_columns: ClassVar[tuple[str, ...]] = ("repo", "number", "title", "state", "user_login")

    repo: str = ""
    number: int
    id: int
    title: str
    state: str
    url: str
    user_login: str | None = None
    is_pull_request: bool = False

    @model_validator(mode="before")
    @classmethod
    def _url(cls, data: Any) -> Any:
        return _with_aliases(data)

    @model_validator(mode="before")
    @classmethod
    def _flatten_issue(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        patch: dict[str, Any] = {}
        if "repo" not in data:
            repo = _repo_from_repository_url(data.get("repository_url"))
            if repo:
                patch["repo"] = repo
        if "user_login" not in data:
            user = data.get("user") or {}
            if isinstance(user, dict):
                patch["user_login"] = user.get("login")
        if "is_pull_request" not in data:
            patch["is_pull_request"] = "pull_request" in data and data["pull_request"] is not None
        return {**data, **patch} if patch else data


class UserHit(Record):
    """One row of ``GET /search/users``."""

    model_config = ConfigDict(extra="ignore")
    table_columns: ClassVar[tuple[str, ...]] = ("login", "type")

    id: int
    login: str
    type: str
    url: str

    @model_validator(mode="before")
    @classmethod
    def _url(cls, data: Any) -> Any:
        return _with_aliases(data)


__all__ = [
    "REPO_HIT_COLUMNS",
    "Code",
    "CorpusRepo",
    "Issue",
    "Repo",
    "SweepFile",
    "SweepMatch",
    "SweepRepo",
    "SyncOutcome",
    "User",
    "UserHit",
    "Worktree",
]
