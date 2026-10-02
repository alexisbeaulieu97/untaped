"""Pure workspace domain: models, naming rules and archive safety."""

from untaped_workspace.domain.models import (
    ArchivedRecord,
    CachedRepo,
    Checkout,
    CommandResult,
    RepoArg,
    RepoSpec,
    ResolvedRepo,
    WorkspaceRecord,
    WorktreeStatus,
)
from untaped_workspace.domain.naming import (
    assign_dirs,
    branch_for,
    looks_like_url,
    repo_identity,
    validate_workspace_name,
)
from untaped_workspace.domain.safety import archive_blockers, archive_hint

__all__ = [
    "ArchivedRecord",
    "CachedRepo",
    "Checkout",
    "CommandResult",
    "RepoArg",
    "RepoSpec",
    "ResolvedRepo",
    "WorkspaceRecord",
    "WorktreeStatus",
    "archive_blockers",
    "archive_hint",
    "assign_dirs",
    "branch_for",
    "looks_like_url",
    "repo_identity",
    "validate_workspace_name",
]
