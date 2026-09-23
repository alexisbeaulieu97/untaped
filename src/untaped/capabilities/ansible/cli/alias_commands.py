"""Alias management commands for the Ansible tool."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.ansible.domain.payloads import AliasOutcome
from untaped.capabilities.ansible.infrastructure import AliasRepository, SourceRepository
from untaped.capabilities.ansible.settings import is_repo_name
from untaped.capability_api import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    OperationCancelledError,
    UiContext,
    UntapedError,
    UsageError,
    YesOption,
    app_context,
    create_app,
    deprecated_alias,
    emit,
    not_found,
    q,
    report_errors,
)

app = create_app(name="alias", help="Manage dependency aliases.")

_KIND = "ansible.alias_outcome"


@app.command(name="set")
def alias_set_command(
    alias: Annotated[str, Parameter(help="Alias to set.")],
    repo: Annotated[str, Parameter(help="Canonical GitHub owner/repo.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Map an Ansible role/Galaxy name to a GitHub owner/repo (creates or replaces)."""
    with report_errors():
        if not is_repo_name(repo):
            raise UsageError(f"alias target must be a GitHub owner/name repo (got {q(repo)})")
        aliases = AliasRepository()
        previous = aliases.entries().get(alias)
        action = "unchanged" if previous == repo else "updated" if previous else "created"
        if action != "unchanged":
            aliases.set(alias, repo)
        emit(
            AliasOutcome(action=action, alias=alias, repo=repo).model_dump(mode="json"),
            fmt=fmt,
            columns=columns,
            kind=_KIND,
        )
        if action != "unchanged":
            _warn_saved_sources_need_refresh(app_context().ui(strict=False))


@app.command(name="list")
def alias_list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List dependency aliases."""
    with report_errors():
        rows: list[dict[str, object]] = [
            {"alias": alias, "repo": repo}
            for alias, repo in sorted(AliasRepository().entries().items())
        ]
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="ansible.alias",
            empty="No dependency aliases configured. Map one with "
            "`untaped ansible alias set NAME OWNER/REPO`.",
        )


@app.command(name="remove")
def alias_remove_command(
    alias: Annotated[str, Parameter(help="Alias to remove.")],
    /,
    *,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Remove a dependency alias."""
    with report_errors():
        aliases = AliasRepository()
        entries = aliases.entries()
        repo = entries.get(alias)
        if repo is None:
            raise UntapedError(not_found("alias", alias, known=sorted(entries)))
        ui = app_context().ui(strict=False)
        if not dry_run:
            confirmed = ui.confirm_action(
                f"Remove alias {q(alias)} -> {repo}?",
                assume_yes=yes,
                refusal="alias remove requires --yes when not interactive",
            )
            if not confirmed:
                raise OperationCancelledError
            aliases.remove(alias)
        emit(
            AliasOutcome(
                action="planned" if dry_run else "deleted", alias=alias, repo=repo
            ).model_dump(mode="json"),
            fmt=fmt,
            columns=columns,
            kind=_KIND,
        )
        if not dry_run:
            _warn_saved_sources_need_refresh(ui)


deprecated_alias(app, "add", "set")


def _warn_saved_sources_need_refresh(ui: UiContext) -> None:
    """Aliases are baked into cached source snapshots at refresh time."""
    names = [source.name for source in SourceRepository().entries()]
    if not names:
        return
    commands = ", ".join(f"`untaped ansible source refresh {name}`" for name in names)
    ui.message(
        "warning",
        "cached source data resolves aliases at refresh time; "
        f"run {commands} for this change to affect cached graphs",
    )
