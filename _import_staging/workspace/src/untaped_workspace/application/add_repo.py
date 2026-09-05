"""Use case: append a repo to a workspace's manifest."""

from __future__ import annotations

from untaped_workspace.application.ports import ManifestRepository
from untaped_workspace.domain import DuplicateRepoName, DuplicateRepoUrl, Repo, Workspace
from untaped_workspace.errors import WorkspaceError


class AddRepo:
    def __init__(self, manifest_repo: ManifestRepository) -> None:
        self._manifests = manifest_repo

    def __call__(
        self,
        workspace: Workspace,
        *,
        url: str,
        repo_name: str | None = None,
        branch: str | None = None,
    ) -> Repo:
        manifest = self._manifests.read(workspace.path)
        repo = Repo.model_validate({"url": url, "name": repo_name, "branch": branch})
        try:
            new_manifest = manifest.add_repo(repo)
        except DuplicateRepoUrl as exc:
            raise WorkspaceError(f"repo already in workspace {workspace.name!r}: {url}") from exc
        except DuplicateRepoName as exc:
            base = (
                f"repo name {exc.existing.name!r} already in use in workspace "
                f"{workspace.name!r} by {exc.existing.url}"
            )
            # `not repo_name` mirrors `Repo._fill_default_name` — an
            # explicit empty string is treated the same as omission
            # (both produce a derived name), so the disambiguation hint
            # only fires when the user did not pass `--repo-name`.
            if not repo_name:
                raise WorkspaceError(f"{base}; pass --repo-name to disambiguate") from exc
            raise WorkspaceError(base) from exc
        self._manifests.write(workspace.path, new_manifest)
        return repo
