"""Records the git plugin emits: hosts, store report rows and tree entries."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Column, Record


class Host(Record):
    """One Git host a plugin supplies credentials for (``untaped git hosts``).

    ``helpers_first`` are the credential helpers the user's git config asks
    before untaped's own, in untaped's worktrees; ``missing_helper`` names an
    untaped helper path written into a worktree config that no longer exists.
    """

    host: Annotated[str, Column]
    plugins: Annotated[list[str], Column]
    credential: Annotated[bool, Column]
    helpers_first: Annotated[list[str], Column] = Field(default_factory=list)
    missing_helper: str | None = None


class RepoCount(BaseModel):
    """A number of store repos and what they take on disk."""

    model_config = ConfigDict(frozen=True)

    repos: int = 0
    size_bytes: int = 0

    def add(self, size: int) -> RepoCount:
        return RepoCount(repos=self.repos + 1, size_bytes=self.size_bytes + size)


class StoreReport(Record):
    """What the repo store holds and costs, read from disk alone (no git per repo).

    ``used_by`` maps a plugin to the repos it uses (a private file or an owned
    worktree there); ``exclusive_bytes`` to the size of the repos only it
    uses, what releasing them would free. ``unowned`` repos carry an
    interrupted release's mark (finished on their next use);
    ``held_by_branches`` repos are used by no plugin, kept by a branch or
    stash a release left. ``filter_ignored`` maps a host to its repos whose
    server ignored the partial-clone filter (they hold every blob);
    ``gc_log`` lists repos where git paused automatic maintenance after a
    failure.
    """

    store_dir: str
    repos: int
    size_bytes: int
    used_by: dict[str, int] = Field(default_factory=dict)
    exclusive_bytes: dict[str, int] = Field(default_factory=dict)
    unowned: RepoCount = Field(default_factory=RepoCount)
    held_by_branches: RepoCount = Field(default_factory=RepoCount)
    git_version: str | None = None
    packs_median: float = 0
    packs_max: int = 0
    loose_objects: int = 0
    filter_ignored: dict[str, int] = Field(default_factory=dict)
    gc_log: list[str] = Field(default_factory=list)


class TreeEntry(Record):
    """One ``ls-tree`` entry of a store tree."""

    mode: Annotated[str, Column]
    type: Annotated[str, Column]
    oid: str
    path: Annotated[str, Column]
