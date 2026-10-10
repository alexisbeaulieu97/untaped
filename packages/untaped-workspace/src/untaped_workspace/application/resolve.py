"""``workspace repos resolve``: ask each repo's source again and save the URL it lists now.

A repo's URL is frozen when the workspace is created, so a provider that
changes how its URLs look (``github.git_protocol``, a moved host) changes
nothing until this runs. A new URL must name the same store repo: a
worktree is bound to its store repo, so a URL with another store key is
refused, never followed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import UntapedError, note_failure
from untaped_workspace.application.locate import active_workspace, workspace_root
from untaped_workspace.domain.models import RepoSpec
from untaped_workspace.domain.naming import repo_key
from untaped_workspace.domain.records import ResolveAction, ResolveOutcome

if TYPE_CHECKING:
    from untaped_workspace.application.ports import GitWorktrees, RepoCatalog, WorkspaceStore

_FIELDS = ("url", "source", "default_branch", "description", "archived")


class ResolveRepos:
    """Re-ask each repo's source, under the workspace lock, and save what changed."""

    def __init__(
        self,
        store: WorkspaceStore,
        git: GitWorktrees,
        catalog: RepoCatalog,
        *,
        workspaces_dir: Path,
    ) -> None:
        self._store = store
        self._git = git
        self._catalog = catalog
        self._workspaces_dir = workspaces_dir

    def __call__(self, name: str) -> list[ResolveOutcome]:
        with self._store.locked(name):
            record = active_workspace(self._store, name)
            root = workspace_root(self._workspaces_dir, record.name)
            rows: list[ResolveOutcome] = []
            changed: list[RepoSpec] = []
            for spec in record.repos:
                row, new = self._one(record.name, root, spec)
                rows.append(row)
                if new is not None:
                    changed.append(new)
            if changed:
                self._store.update_repos(record.name, changed)
                for spec in changed:
                    self._git.configure(spec.url, root / spec.dir)
            return rows

    def _one(
        self, workspace: str, root: Path, spec: RepoSpec
    ) -> tuple[ResolveOutcome, RepoSpec | None]:
        def row(
            action: ResolveAction,
            detail: str = "",
            *,
            url: str = spec.url,
            error: UntapedError | None = None,
        ) -> ResolveOutcome:
            return ResolveOutcome(
                workspace=workspace,
                repo=spec.name,
                dir=spec.dir,
                action=action,
                url=url,
                detail=detail,
                target_path=root / spec.dir,
                error=None if error is None else note_failure(error, message=detail),
            )

        if spec.source is None:
            return row("skipped", "a typed URL: no source to ask"), None
        try:
            listed = self._catalog.reask(spec)
        except UntapedError as exc:
            return row("failed", str(exc), error=exc), None
        if listed is None:
            return row("skipped", f"{spec.source.plugin} is not installed or not ready"), None
        if all(getattr(listed, field) == getattr(spec, field) for field in _FIELDS):
            return row("unchanged"), None
        if repo_key(listed.url) != repo_key(spec.url):
            detail = (
                f"{spec.source.plugin} now lists {listed.url}, another store repo than {spec.url}; "
                f"remove {spec.dir} from the workspace and add it again"
            )
            return row("failed", detail, url=listed.url), None
        new = spec.model_copy(update={field: getattr(listed, field) for field in _FIELDS})
        detail = "" if listed.url == spec.url else f"was {spec.url}"
        return row("updated", detail, url=listed.url), new
