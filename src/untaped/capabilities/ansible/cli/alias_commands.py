"""Alias management commands for the Ansible tool."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.api import (
    ColumnsOption,
    FormatOption,
    UntapedError,
    create_app,
    echo,
    emit,
    report_errors,
)
from untaped.capabilities.ansible.infrastructure import AliasRepository, SourceRepository
from untaped.capabilities.ansible.settings import is_repo_name

app = create_app(name="alias", help="Manage dependency aliases.")


@app.command(name="add")
def alias_add_command(
    alias: Annotated[str, Parameter(help="Alias to set.")],
    repo: Annotated[str, Parameter(help="Canonical GitHub owner/repo.")],
) -> None:
    """Map an Ansible role/Galaxy name to a GitHub owner/repo."""
    with report_errors():
        if not is_repo_name(repo):
            raise UntapedError(f"alias target must be a GitHub owner/name repo (got {repo!r})")
        AliasRepository().set(alias, repo)
        echo(f"set alias {alias!r} -> {repo}", err=True)
        _warn_saved_sources_need_refresh()


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
            "`untaped ansible alias add <name> <repo>`.",
        )


@app.command(name="remove")
def alias_remove_command(alias: Annotated[str, Parameter(help="Alias to remove.")]) -> None:
    """Remove a dependency alias."""
    with report_errors():
        removed = AliasRepository().remove(alias)
        if not removed:
            raise UntapedError(f"unknown alias: {alias!r}")
        echo(f"removed alias {alias!r}", err=True)
        _warn_saved_sources_need_refresh()


def _warn_saved_sources_need_refresh() -> None:
    """Aliases are baked into cached source snapshots at refresh time."""
    names = [source.name for source in SourceRepository().entries()]
    if not names:
        return
    commands = ", ".join(f"`untaped ansible source refresh {name}`" for name in names)
    echo(
        "warning: cached source data resolves aliases at refresh time; "
        f"run {commands} for this change to affect cached graphs",
        err=True,
    )
