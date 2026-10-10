"""Git command tree: ``credential`` (git's helper protocol) and ``hosts``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter

from untaped.sdk import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    emit,
    read_stdin,
    report_errors,
)
from untaped_git.settings import git_settings

app = create_app(
    name="git",
    help="Credentials for Git hosts, and the repo store other plugins keep repositories in.",
)

HOST = "git.host"


@app.command(name="credential")
def credential_command(
    action: Annotated[
        Literal["get", "store", "erase"],
        Parameter(help="The credential helper action git asks for."),
    ],
    /,
) -> None:
    """Answer Git's credential requests from the plugins that know the host.

    Git runs this as a credential helper (untaped writes it into its own
    worktrees): it reads `protocol`, `host` and `path` on stdin and prints a
    username and password when a plugin supplies them for that URL. `store`
    and `erase` do nothing: untaped's credentials live in its own settings.
    """
    from untaped_git.domain.hosts import resolve_host  # noqa: PLC0415

    with report_errors():
        if action != "get":
            return
        fields = _read_fields()
        protocol, host = fields.get("protocol"), fields.get("host")
        if protocol != "https" or not host:
            return
        auth = resolve_host(f"https://{host}/{fields.get('path', '')}")
        if auth is None or auth.credential is None:
            return
        echo(f"username={auth.credential.username}")
        echo(f"password={auth.credential.password.get_secret_value()}")


@app.command(name="hosts")
def hosts_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List the Git hosts plugins supply credentials for.

    Each row names the plugins that claim the host, whether a credential is
    available, the credential helpers your own git config asks before
    untaped's in its worktrees, and an untaped helper path that no longer
    exists.
    """
    from untaped_git.application.hosts import list_hosts  # noqa: PLC0415
    from untaped_git.infrastructure.helpers import missing_helper, user_helpers  # noqa: PLC0415

    with report_errors():
        root = git_settings().store_dir.expanduser()
        cwd = root if root.is_dir() else Path(root.anchor)
        rows = list_hosts(
            helpers_for=lambda host: user_helpers(host, cwd=cwd),
            missing_helper=missing_helper(root),
        )
        emit(rows, fmt=fmt, columns=columns, kind=HOST)


def _read_fields() -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in read_stdin():
        key, sep, value = line.partition("=")
        if sep:
            fields[key] = value
    return fields
