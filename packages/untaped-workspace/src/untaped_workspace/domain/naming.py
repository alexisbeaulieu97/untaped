"""Naming rules: workspace names, repo identities, directory names, branches."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from untaped.sdk import UsageError, first_validation_error, q, safe_path_segment
from untaped_workspace.domain.models import RepoSpec
from untaped_workspace.domain.repo import Repo


def validate_workspace_name(name: str) -> str:
    """Return ``name`` if it is one safe path segment, else raise ``UsageError``."""
    if not name or name.startswith(".") or safe_path_segment(name) != name:
        raise UsageError(f"invalid workspace name {q(name)}: use letters, digits, '.', '_' or '-'")
    return name


def looks_like_url(ident: str) -> bool:
    """Whether ``ident`` is a URL, scp-style ``user@host:path``, a path, or ends in ``.git``."""
    from untaped_git.api import repo_url_parts  # noqa: PLC0415  # keeps CLI startup free of git

    return (
        "://" in ident
        or ident.startswith(("/", "~"))
        or ident.endswith(".git")
        or repo_url_parts(ident)[0] is not None
    )


def repo_key(url: str) -> tuple[str, ...]:
    """Which repo ``url`` names: its repo store key.

    The store keeps one repo per key (the https and ssh URLs of a repo, and
    two spellings of its path, are one), so workspace counts them as one too.
    """
    from untaped_git.api import store_key  # noqa: PLC0415  # keeps CLI startup free of git

    return store_key(url)


def workspace_match(name: str, ident: str) -> bool:
    """Whether the repo ``name`` is what the user typed: ``owner/name`` (any depth) exactly,
    else a bare name against the last segment; case-insensitive."""
    wanted = ident.lower()
    if "/" in ident:
        return name.lower() == wanted
    return name.rpartition("/")[2].lower() == wanted


def typed_repo(url: str) -> Repo:
    """The repo a typed URL names: a plain :class:`Repo` called by the URL's full path.

    No provider is asked; credentials still come from whoever fills the
    git plugin's ``GitHost`` for the URL's host. A URL untaped refuses
    (a path, ``file://``, credentials in it) is a :class:`UsageError`.
    """
    from untaped_git.api import repo_url_parts  # noqa: PLC0415  # keeps CLI startup free of git

    _, segments = repo_url_parts(url)
    name = "/".join(segments) or url
    try:
        return Repo(name=name, url=url)
    except ValidationError as exc:
        raise UsageError(
            f"repo {q(url)}: {first_validation_error(exc)}",
            hint="pass an https:// or ssh:// clone URL, or a repo name",
        ) from None


def repo_identity(url: str) -> tuple[str, str]:
    """``(owner, name)`` from a clone URL or path; owner is ``""`` when absent."""
    from untaped_git.api import repo_url_parts  # noqa: PLC0415  # keeps CLI startup free of git

    _, segments = repo_url_parts(url)
    name = segments[-1] if segments else url
    owner = segments[-2] if len(segments) > 1 else ""
    return owner, name


def assign_dirs(new: Sequence[tuple[str, str]], existing: Sequence[RepoSpec]) -> list[str]:
    """Directory names for ``new`` repos: the repo name, ``owner-name`` on a clash."""
    taken = {spec.dir for spec in existing}
    dirs: list[str] = []
    for owner, name in new:
        clash = name in taken
        candidate = safe_path_segment(f"{owner}-{name}" if clash and owner else name)
        while candidate in taken:
            candidate = f"{candidate}-{len(taken)}"
        taken.add(candidate)
        dirs.append(candidate)
    return dirs


def branch_for(template: str, workspace: str) -> str:
    """The branch a writable repo gets: ``template`` with ``{name}`` filled in."""
    return template.replace("{name}", workspace)
