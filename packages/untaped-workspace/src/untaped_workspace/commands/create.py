"""``untaped workspace create|add``: check repos out into a workspace as git worktrees."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Annotated

from pydantic import Field

from untaped.sdk import (
    Output,
    Parallel,
    Positional,
    ProgressHandle,
    Short,
    Stdin,
    UsageError,
    now,
    plural,
    q,
)
from untaped_workspace.api import Repo, RepoSource
from untaped_workspace.application.provision import ProvisionRepos
from untaped_workspace.commands._workspace import NAME_HELP, WORKERS, WorkspaceCommand
from untaped_workspace.commands.picker import Picker, choose_repos
from untaped_workspace.domain.models import RepoArg
from untaped_workspace.infrastructure import StateWorkspaceStore
from untaped_workspace.records import RepoOutcome

_EMPTY_HINT = "drop --empty, or --repo, --read-only, --stdin, --branch and --base"


class Provisions(WorkspaceCommand):
    """What ``create`` and ``add`` share: the repos to check out, and how."""

    repo: Annotated[list[str], Short("-r")] = Field(
        default_factory=list,
        description=(
            "Repo to check out on the workspace branch: owner/name, a name unique among "
            "the repos your plugins list, or a clone URL (repeatable)."
        ),
    )
    read_only: list[str] = Field(
        default_factory=list,
        description="Repo to check out read-only, detached at its base (repeatable).",
    )
    branch: str | None = Field(
        None,
        description=(
            "Branch for every writable repo. Default: the workspace.branch_template setting."
        ),
    )
    base: str | None = Field(
        None,
        description=(
            "Base branch or tag for every repo, read-only ones too. "
            "Default: each repo's default branch."
        ),
    )
    stdin: Stdin[Repo] = Stdin(via=RepoSource.to_repo)
    parallel: Parallel = WORKERS
    output: Output[RepoOutcome]

    def flagged(self) -> list[RepoArg]:
        """``--repo`` then ``--stdin`` repos (writable), then ``--read-only`` repos."""
        args = [RepoArg(ident=ident, branch=self.branch, base=self.base) for ident in self.repo]
        if self.stdin.requested:
            args += [
                RepoArg(ident=value, branch=self.branch, base=self.base)
                if isinstance(value, str)
                else RepoArg(ident=value.name, repo=value, branch=self.branch, base=self.base)
                for value in self.stdin.values()
            ]
        args += [RepoArg(ident=ident, read_only=True, base=self.base) for ident in self.read_only]
        return args

    def picker(self, store: StateWorkspaceStore) -> Picker:
        return Picker(
            store=store,
            root=self.workspaces_dir,
            git=self.git_worktrees(),
            branch_template=self.settings.branch_template,
        )

    def provisioner(self, warn: Callable[[str], None]) -> ProvisionRepos:
        """The ``create``/``add`` use case wired to the real adapters."""
        from untaped_workspace.infrastructure.catalog import RepoSources  # noqa: PLC0415

        return ProvisionRepos(
            StateWorkspaceStore(),
            self.git_worktrees(),
            RepoSources(),
            workspaces_dir=self.workspaces_dir,
            branch_template=self.settings.branch_template,
            parallel=self.parallel.value,
            now=now,
            warn=warn,
        )

    def shown(self, rows: Sequence[RepoOutcome], name: str, warnings: Sequence[str]) -> None:
        """Say how it went on stderr: the stopped backfills, the count ready, each failure."""
        for warning in warnings:
            self.ctx.diag.warning(warning)
        if self.output.format == "table":
            ready = sum(1 for row in rows if not row.failed)
            self.ctx.diag.success(f"workspace {q(name)}: {plural(ready, 'repo')} ready")
        self.report(rows, lambda row: f"{name}/{row.dir}")


class Create(Provisions):
    """Create a workspace and check out its repos as git worktrees.

    Without --repo, --read-only or --stdin, a terminal opens the repo picker,
    which also confirms with no repos selected; without a terminal, or with
    --empty, `create NAME` makes an empty workspace (for `workspace add`),
    and `create` with no NAME exits 2. --empty with repo options, --branch
    or --base exits 2.

    Each repo is one row: `created` (a new branch from the base),
    `checked_out` (an existing branch resumed, or a read-only checkout) or
    `failed`. Repos that succeed stay even when another fails (exit 1);
    `add` retries the failed ones. An unknown or ambiguous repo name exits 2
    with candidates. --stdin reads names or URLs, or `workspace.repo` records
    and the repo records a repo source reads (`github.repo`, say); a record
    without a kind, or of a kind no source reads, exits 2.

    Each row's `target_path` is its worktree; `untaped workspace path NAME`
    prints the workspace's folder. A name whose directory already holds
    files exits 1. How repos are named, and
    how branches and bases are chosen: `untaped workspace --help`.

    Examples
    --------
    Two repos on the task branch, and one read-only for reference:

    ```console
    $ untaped workspace create PROJ-123 --repo acme/api --repo acme/web --read-only acme/docs
    ```

    Repo names piped one per line:

    ```console
    $ printf 'acme/api\nacme/web\n' | untaped workspace create PROJ-123 --stdin
    ```
    """

    name: Positional[str | None] = Field(
        None,
        description=(
            "Name of the workspace to create. Optional in a terminal without repo "
            "options: the repo picker asks for it."
        ),
    )
    empty: bool = Field(
        False, description="Make an empty workspace to `add` repos to later, without the picker."
    )

    def run(self) -> Iterator[RepoOutcome]:
        repo_flags = (self.repo, self.read_only, self.stdin.requested, self.branch, self.base)
        if self.empty and any(repo_flags):  # before --stdin is read
            raise UsageError("--empty takes no repos", hint=_EMPTY_HINT)
        store = StateWorkspaceStore()
        name, args = choose_repos(
            self.ctx.terminal,
            self.flagged(),
            name=self.name,
            branch=self.branch,
            base=self.base,
            empty=self.empty,
            picker=self.picker(store),
        )
        warnings: list[str] = []
        provision = self.provisioner(warnings.append)
        with self.ctx.diag.progress(f"Creating workspace {name}") as progress:
            rows = provision.create(name, args, on_done=_progress_line(progress), progress=progress)
        self.shown(rows, name, warnings)
        return iter(rows)


class Add(Provisions):
    """Check out more repos into an existing workspace; present ones report unchanged.

    Without --repo, --read-only or --stdin, a terminal opens the repo picker;
    without a terminal, that exits 2. Rows, repo names and --stdin work as
    on `create`.

    Examples
    --------
    ```console
    $ untaped workspace add PROJ-123 --repo acme/infra
    ```
    """

    name: Positional[str | None] = Field(None, description=NAME_HELP)

    def run(self) -> Iterator[RepoOutcome]:
        record = self.locate(self.name)
        _, args = choose_repos(
            self.ctx.terminal,
            self.flagged(),
            name=record.name,
            record=record,
            branch=self.branch,
            base=self.base,
            picker=self.picker(StateWorkspaceStore()),
        )
        warnings: list[str] = []
        provision = self.provisioner(warnings.append)
        with self.ctx.diag.progress(f"Adding repos to {record.name}") as progress:
            rows = provision.add(record, args, on_done=_progress_line(progress), progress=progress)
        self.shown(rows, record.name, warnings)
        return iter(rows)


def _progress_line(progress: ProgressHandle) -> Callable[[RepoOutcome, int, int], None]:
    """``on_done`` for provisioning: the count and the repo of each finished checkout."""

    def update(row: RepoOutcome, done: int, total: int) -> None:
        progress.update(row.repo, done=done, total=total)

    return update
