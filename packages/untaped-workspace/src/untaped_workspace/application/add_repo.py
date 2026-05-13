"""Use case: append a repo to a workspace's manifest."""

from __future__ import annotations

from untaped_workspace.application.ports import ManifestRepository
from untaped_workspace.domain import Repo, Workspace
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
        if manifest.repo_by_url(url) is not None:
            raise WorkspaceError(f"repo already in workspace {workspace.name!r}: {url}")
        repo = Repo.model_validate({"url": url, "name": repo_name, "branch": branch})
        try:
            new_manifest = manifest.add_repo(repo)
        except ValueError as exc:
            # The aggregate's validator caught a duplicate name (the
            # url-collision case is already handled above). Look up the
            # incumbent to build the CLI-facing message, and add the
            # `--repo-name` disambiguation hint when the caller did not
            # pass one explicitly — `not repo_name` mirrors
            # ``Repo._fill_default_name``'s check so an explicit empty
            # string is treated as omission.
            existing = manifest.repo_by_name(repo.name)
            base = (
                f"repo name {repo.name!r} already in use in workspace "
                f"{workspace.name!r} by {existing.url if existing else '<unknown>'}"
            )
            if not repo_name:
                raise WorkspaceError(f"{base}; pass --repo-name to disambiguate") from exc
            raise WorkspaceError(base) from exc
        self._manifests.write(workspace.path, new_manifest)
        return repo
