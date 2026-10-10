"""Pure workspace domain: models, naming rules, and archive and remove safety."""

from untaped_workspace.domain.models import (
    ArchivedRecord,
    Checkout,
    CommandResult,
    LocalBranch,
    RepoArg,
    RepoRelease,
    RepoSpec,
    StoredRepo,
    StoreUse,
    WorkspaceRecord,
    WorktreeStatus,
)
from untaped_workspace.domain.naming import (
    assign_dirs,
    branch_for,
    looks_like_url,
    repo_identity,
    repo_key,
    typed_repo,
    validate_workspace_name,
    workspace_match,
)
from untaped_workspace.domain.safety import (
    archive_blockers,
    archive_hint,
    branch_work,
    releasable_branches,
    unpushed_branch_blocker,
)

__all__ = [
    "ArchivedRecord",
    "Checkout",
    "CommandResult",
    "LocalBranch",
    "RepoArg",
    "RepoRelease",
    "RepoSpec",
    "StoreUse",
    "StoredRepo",
    "WorkspaceRecord",
    "WorktreeStatus",
    "archive_blockers",
    "archive_hint",
    "assign_dirs",
    "branch_for",
    "branch_work",
    "looks_like_url",
    "releasable_branches",
    "repo_identity",
    "repo_key",
    "typed_repo",
    "unpushed_branch_blocker",
    "validate_workspace_name",
    "workspace_match",
]
