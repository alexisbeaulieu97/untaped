"""The interactive repo picker behind ``create``/``add`` when no repo flag is given.

Builds the core :class:`PickRequest` from a :class:`RepoPickSource` and turns
the confirmed :class:`PickResult` into :class:`RepoArg` values. The picker
opens only in a terminal and only without ``--repo``/``--read-only``/``--stdin``;
agents always pass flags. It runs before the workspace lock is taken.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from untaped.capabilities.workspace.application.ports import WorkspaceStore
from untaped.capabilities.workspace.application.provision import refuse_occupied
from untaped.capabilities.workspace.cli.common import (
    NO_NAME_HINT,
    NO_REPOS_HINT,
    git_worktrees,
    workspaces_dir,
)
from untaped.capabilities.workspace.cli.common import repo_args as flag_repo_args
from untaped.capabilities.workspace.domain.models import RepoArg, WorkspaceRecord
from untaped.capabilities.workspace.domain.naming import (
    branch_for,
    looks_like_url,
    repo_key,
    validate_workspace_name,
)
from untaped.capabilities.workspace.infrastructure.pick_source import RepoPickSource
from untaped.capabilities.workspace.settings import WorkspaceSettings
from untaped.capability_api import (
    PickCatalog,
    PickItem,
    PickRequest,
    PickResult,
    PickSetting,
    UiContext,
    UntapedError,
    UsageError,
    q,
)

READ_ONLY = "read-only"


def build_request(
    *,
    heading: str,
    source: RepoPickSource,
    template: str,
    title: str,
    title_label: str,
    validate_title: Callable[[str], str | None] | None,
    branch: str = "",
    base: str = "",
) -> PickRequest:
    """The picker request: ``mode``/``base``/``branch`` settings over ``source``'s catalog.

    ``branch``/``base`` (the ``--branch``/``--base`` flags) seed the all-items
    defaults. The subtitle previews the branch writable repos get.
    """

    def subtitle(current: str, defaults: Mapping[str, str]) -> str:
        return defaults.get("branch") or branch_for(template, current or "NAME")

    def refresh(force: bool) -> PickCatalog:
        return source.catalog(refresh=True if force else None)

    return PickRequest(
        heading=heading,
        catalog=source.catalog(refresh=False),
        settings=(
            PickSetting(key="mode", label="mode", default="write", choices=("write", READ_ONLY)),
            PickSetting(
                key="base",
                label="base",
                default=base,
                placeholder="default",
                complete=source.branches,
            ),
            PickSetting(key="branch", label="branch", default=branch, placeholder="from template"),
        ),
        title=title,
        title_label=title_label,
        validate_title=validate_title,
        subtitle=subtitle,
        refresh=refresh,
        adhoc=_adhoc,
    )


def _adhoc(query: str) -> PickItem | None:
    """A typed git URL becomes an extra item."""
    if looks_like_url(query):
        return PickItem(id=query, label=query, description="git URL")
    return None


def repo_args(result: PickResult) -> list[RepoArg]:
    """One :class:`RepoArg` per pick; empty settings fall back to the workspace defaults."""
    return [
        RepoArg(
            ident=picked.item.id,
            read_only=picked.settings.get("mode") == READ_ONLY,
            branch=picked.settings.get("branch") or None,
            base=picked.settings.get("base") or None,
        )
        for picked in result.picks
    ]


def name_validator(store: WorkspaceStore, root: Path) -> Callable[[str], str | None]:
    """Checks a typed workspace name; returns why ``create`` would refuse it, else ``None``.

    Invalid, already active, or a non-empty directory under ``root`` (the
    workspaces dir): caught in the picker, so the selection is not lost.
    """

    def check(name: str) -> str | None:
        try:
            validate_workspace_name(name)
        except UsageError as exc:
            return str(exc)
        if store.get(name) is not None:
            return f"workspace {q(name)} already exists"
        try:
            refuse_occupied(store, root, name)
        except UntapedError as exc:
            return str(exc)
        return None

    return check


def choose_repos(
    ui: UiContext,
    settings: WorkspaceSettings,
    store: WorkspaceStore,
    *,
    name: str | None,
    record: WorkspaceRecord | None = None,
    repo: list[str] | None,
    read_only: list[str] | None,
    branch: str | None,
    base: str | None,
    stdin: bool,
) -> tuple[str, list[RepoArg]]:
    """The workspace name and repos: from the flags, else from the picker in a terminal.

    ``record`` is the workspace ``add`` targets (``None`` for ``create``).
    Without a terminal and without flags this is a usage error naming them.
    """
    name = record.name if record is not None else name
    flags = bool(repo or read_only or stdin)
    if flags or not ui.can_prompt:
        if name is None:
            hint = "pass NAME before the options" if flags else NO_NAME_HINT
            raise UsageError("a workspace name is required", hint=hint)
        if not flags:
            raise UsageError("no repos given", hint=NO_REPOS_HINT)
        return name, flag_repo_args(repo, read_only, branch=branch, base=base, stdin=stdin)
    return _pick(ui, settings, store, name=name, record=record, branch=branch, base=base)


def _pick(
    ui: UiContext,
    settings: WorkspaceSettings,
    store: WorkspaceStore,
    *,
    name: str | None,
    record: WorkspaceRecord | None,
    branch: str | None,
    base: str | None,
) -> tuple[str, list[RepoArg]]:
    """Open the picker: ``create`` asks for the name too, ``add`` hides present repos."""
    validate: Callable[[str], str | None] | None = None
    exclude: set[tuple[str, ...]] = set()
    fixed_name: str | None = None
    if record is None:
        validate = name_validator(store, workspaces_dir(settings))
        if name and (problem := validate(name)):
            raise UsageError(problem)
        heading, template = "New workspace", settings.branch_template
        title, title_label = name or "", "name"
    else:  # add: no name field; the preview uses the workspace's name
        heading = f"Add to {record.name}"
        template = branch_for(settings.branch_template, record.name)
        title, title_label, fixed_name = "", "", record.name
        exclude = {repo_key(spec.url) for spec in record.repos}
    source = RepoPickSource(git=git_worktrees(settings), exclude=exclude)
    request = build_request(
        heading=heading,
        source=source,
        template=template,
        title=title,
        title_label=title_label,
        validate_title=validate,
        branch=branch or "",
        base=base or "",
    )
    result = ui.pick_many(request)
    args = [source.pick_arg(arg) for arg in repo_args(result)]
    return fixed_name or result.title.strip() or title, args
