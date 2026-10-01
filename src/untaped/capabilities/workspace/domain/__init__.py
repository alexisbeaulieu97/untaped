"""Pure workspace domain: models, naming rules and archive safety."""

from untaped.capabilities.workspace.domain.models import (
    ArchivedRecord,
    Checkout,
    CommandResult,
    RepoArg,
    RepoSpec,
    ResolvedRepo,
    WorkspaceRecord,
    WorktreeStatus,
)
from untaped.capabilities.workspace.domain.naming import (
    assign_dirs,
    branch_for,
    looks_like_url,
    repo_identity,
    repo_key,
    validate_workspace_name,
)
from untaped.capabilities.workspace.domain.safety import archive_blockers, archive_hint

__all__ = [
    "ArchivedRecord",
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
    "repo_key",
    "validate_workspace_name",
]
