"""The interactive repo picker behind ``create``/``add`` when no repo flag is given.

Builds the core :class:`PickRequest` from a :class:`RepoPickSource` and turns
the confirmed :class:`PickResult` into :class:`RepoArg` values. The picker
opens only in a terminal and only without ``--repo``/``--read-only``/``--stdin``;
agents always pass flags. It runs before the workspace lock is taken.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from untaped.sdk import (
    PickCatalog,
    PickItem,
    PickRequest,
    PickResult,
    PickSetting,
    UiContext,
    UntapedError,
    UsageError,
    command_line,
    q,
)
from untaped_workspace.application.ports import GitWorktrees, WorkspaceStore
from untaped_workspace.application.provision import refuse_occupied
from untaped_workspace.domain.models import RepoArg, WorkspaceRecord
from untaped_workspace.domain.naming import (
    branch_for,
    looks_like_url,
    repo_key,
    validate_workspace_name,
)

if TYPE_CHECKING:
    from untaped_workspace.infrastructure.pick_source import RepoPickSource

NO_REPOS_HINT = "pass --repo OWNER/NAME (repeatable) or --stdin; the repo picker needs a terminal"
NO_NAME_HINT = "pass NAME and --repo OWNER/NAME (or --stdin); the repo picker needs a terminal"

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
    allow_empty: bool = False,
    command: str = "",
) -> PickRequest:
    """The picker request: ``mode``/``base``/``branch`` settings over ``source``'s catalog.

    ``branch``/``base`` (the ``--branch``/``--base`` flags) seed the all-items
    defaults. The subtitle previews the branch writable repos get.
    ``allow_empty`` lets the picker confirm with no repos (``create``).
    ``command`` (``untaped workspace create``) and its repo flags are what the
    no-terminal refusal names.
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
        allow_empty=allow_empty,
        command=command,
        alternative=f"{command} NAME --repo OWNER/NAME" if command else "",
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
    flagged: list[RepoArg],
    *,
    name: str | None,
    record: WorkspaceRecord | None = None,
    branch: str | None,
    base: str | None,
    empty: bool = False,
    picker: Picker,
) -> tuple[str, list[RepoArg]]:
    """The workspace name and repos: from the flags, else from the picker in a terminal.

    ``flagged`` holds the repos ``--repo``, ``--stdin`` and ``--read-only``
    named. ``record`` is the workspace ``add`` targets (``None`` for
    ``create``). Without a terminal and without flags, ``create`` makes an
    empty workspace and ``add`` is a usage error naming the flags. ``empty``
    (``create --empty``, which ``create`` checks came with no repo flags)
    makes an empty workspace without the picker.
    """
    name = record.name if record is not None else name
    if empty or flagged or not ui.can_prompt:
        if name is None:
            hint = "pass NAME before the options" if flagged or empty else NO_NAME_HINT
            raise UsageError("a workspace name is required", hint=hint)
        if not flagged and record is not None:
            raise UsageError("no repos given", hint=NO_REPOS_HINT)
        return name, flagged  # create NAME alone: an empty workspace
    return _pick(ui, picker, name=name, record=record, branch=branch, base=base)


class Picker(NamedTuple):
    """What the picker reads: the store, the workspaces folder, the git adapter, the template."""

    store: WorkspaceStore
    root: Path
    git: GitWorktrees
    branch_template: str


def _pick(
    ui: UiContext,
    picker: Picker,
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
        validate = name_validator(picker.store, picker.root)
        if name and (problem := validate(name)):
            raise UsageError(problem)
        heading, template = "New workspace", picker.branch_template
        title, title_label = name or "", "name"
    else:  # add: no name field; the preview uses the workspace's name
        heading = f"Add to {record.name}"
        template = branch_for(picker.branch_template, record.name)
        title, title_label, fixed_name = "", "", record.name
        exclude = {repo_key(spec.url) for spec in record.repos}
    from untaped_workspace.infrastructure.pick_source import RepoPickSource  # noqa: PLC0415

    source = RepoPickSource(git=picker.git, exclude=exclude)
    from untaped_workspace.commands.create import Add, Create  # noqa: PLC0415 - import cycle

    request = build_request(
        heading=heading,
        source=source,
        template=template,
        title=title,
        title_label=title_label,
        validate_title=validate,
        branch=branch or "",
        base=base or "",
        allow_empty=record is None,
        command=command_line((Create if record is None else Add).spelled()),
    )
    result = ui.pick_many(request)
    args = [source.pick_arg(arg) for arg in repo_args(result)]
    return fixed_name or result.title.strip() or title, args
