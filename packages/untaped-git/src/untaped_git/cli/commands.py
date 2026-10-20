"""Git command tree: ``credential`` (git's helper protocol), ``hosts`` and ``store``."""

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
from untaped_git.settings import git_settings, store_root

app = create_app(
    name="git",
    help="Credentials for Git hosts, and the repo store other plugins keep repositories in.",
)


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

    `get` prints the secret on stdout, where Git reads it: never run it where
    its output is logged. `untaped git hosts` says whether a host has a
    credential without printing it.
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

    A host no plugin claims isn't listed: untaped's worktrees use your own
    Git credentials for it. `credential` false means the plugin has no token
    for the host; `untaped auth set SECTION`, with the plugin's section,
    stores one from a hidden prompt. A helper in `helpers_first` holding a
    stale password answers before untaped; with `git.untaped_helper_only`
    set, untaped's worktrees ask only untaped and the column is empty. A
    host two plugins claim lists both; until one is ranked first, any fetch
    or credential request for it (a push from untaped's worktrees too) is a
    configuration error (exit 4) whose hint names the rank command.
    """
    from untaped_git.application.hosts import list_hosts  # noqa: PLC0415
    from untaped_git.infrastructure.helpers import missing_helper, user_helpers  # noqa: PLC0415

    with report_errors():
        root = store_root()
        cwd = root if root.is_dir() else Path(root.anchor)
        rows = list_hosts(
            # With untaped's helper only, its worktrees reset the user's helpers away.
            helpers_for=lambda host: (
                [] if git_settings().untaped_helper_only else user_helpers(host, cwd=cwd)
            ),
            missing_helper=missing_helper(root),
        )
        emit(rows, fmt=fmt, columns=columns)


@app.command(name="store")
def store_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """Report what the repo store holds and costs, read from disk alone.

    Shows the repos, their size, which plugins use them and the size of the
    repos only one plugin uses (what releasing them would free), repos an
    interrupted release left (finished on their next use) or that only a
    branch or stash keeps, pack counts, hosts that ignore the partial-clone
    filter, and repos where git paused automatic maintenance.

    Each plugin's own delete or remove command releases the repos it uses;
    a repo stays while another plugin, a hand-added worktree, a branch or a
    stash holds it, and untaped never deletes those branches or stashes.
    Never edit a repo under the store by hand: other plugins' refs and
    worktrees share it. Their refs live under `refs/untaped/` and show in
    `git log --all` from an untaped worktree.

    The store needs Git 2.29 or newer; `untaped doctor` checks the version
    and the store.
    """
    from untaped_git.cli.store_view import report_lines  # noqa: PLC0415
    from untaped_git.infrastructure.report import store_report  # noqa: PLC0415
    from untaped_git.infrastructure.version import git_version, version_text  # noqa: PLC0415

    with report_errors():
        version = git_version()
        report = store_report(
            store_root(),
            version=None if version is None else version_text(version),
        )
        if fmt == "table" and columns is None:
            for line in report_lines(report):
                echo(line)
            return
        emit(report, fmt=fmt, columns=columns)


def _read_fields() -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in read_stdin():
        key, sep, value = line.partition("=")
        if sep:
            fields[key] = value
    return fields
