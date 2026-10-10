"""The repo record workspace's ``RepoSource`` contract issues (re-exported by ``api``).

It lives apart from the contract so ``state.yml``'s model loads without the
contracts machinery or the git plugin's api: ``untaped --help`` composes
workspace's state model and imports neither.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, StringConstraints

from untaped.sdk import Issued


def _git_url(value: str) -> str:
    """``value`` when it is a Git URL untaped accepts (``untaped_git.api.validate_git_url``)."""
    from untaped_git.api import validate_git_url  # noqa: PLC0415  # loaded when a URL is checked

    return validate_git_url(value)


#: A remote URL: ``https://``, ``ssh://`` or ``user@host:path``, never a path, ``file://`` or
#: credentials in the URL (the git plugin's ``GitUrl`` rule, checked when a URL is validated).
type GitUrl = Annotated[str, AfterValidator(_git_url)]

#: A repo's name as a user types it: path segments without blanks (``acme/api``).
type RepoName = Annotated[str, StringConstraints(pattern=r"^[^/\s]+(/[^/\s]+)*$")]


class Repo(Issued, kind="workspace.repo"):
    """A repository workspace can check out; ``source`` names the plugin that issued it."""

    name: RepoName
    url: GitUrl
    default_branch: str | None = None
    description: str | None = None
    archived: bool = False
