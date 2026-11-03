"""Repos named by the plugins that list them: ``git.repo``, ``git.repo_selector``, ``RepoSource``.

The git plugin never knows where a repo comes from: every installed plugin
that fills :class:`RepoSource` (a forge's plugin, say) answers, and
:class:`~untaped_git.repo_finder.RepoFinder` picks among their answers by name
(``untaped plugin rank`` orders providers that list the same repo). A typed
URL needs no provider: :func:`typed_repo` names it by its path.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import timedelta
from typing import Annotated

from pydantic import Field, StringConstraints, ValidationError

from untaped.sdk import Issued, Record, UsageError, experimental, first_validation_error, q
from untaped.sdk.contracts import Contract, ProviderRecord, cached
from untaped_git.url import GitUrl, repo_url_parts

#: A repo's name as a user types it: path segments without blanks (``acme/api``).
type RepoName = Annotated[str, StringConstraints(pattern=r"^[^/\s]+(/[^/\s]+)*$")]


@experimental
class Repo(Issued):
    """A repository a plugin lists by name; ``source`` names the plugin that issued it."""

    name: RepoName
    url: GitUrl
    default_branch: str | None = None
    description: str | None = None
    archived: bool = False


@experimental
class RepoSelector(Issued):
    """Which of a provider's repos to take; ``source`` carries the provider's own selector.

    ``match`` holds full-name patterns (``infra/*``) for a provider with no
    selector of its own; a provider that has one (orgs and teams, say) issues
    it through :meth:`RepoSource.to_selector` and reads it back with
    ``self.own(selector, ItsSelector)``.
    """

    match: list[str] = Field(default_factory=list)


@experimental
class RepoSource[T: Record = Repo](Contract):
    """Where the repos you can name in untaped commands come from.

    ``T`` is the provider's own record type; it defaults to Repo.
    """

    def to_repo(self, item: T) -> Repo:
        """Turn the provider's own record into a Repo (only when ``T`` is not Repo)."""
        raise NotImplementedError

    def to_selector(self, item: ProviderRecord) -> RepoSelector:
        """Turn the provider's own selector record into a RepoSelector (optional).

        Annotate the parameter with the selector record it takes; core stamps
        it into the result's ``source``.
        """
        raise NotImplementedError

    @cached(max_age=timedelta(hours=6), label="repos")
    @abstractmethod
    def repos(self) -> list[Repo]:
        """Every repo the provider can list in the active profile."""


@experimental
def looks_like_url(ident: str) -> bool:
    """Whether ``ident`` is a URL, scp-style ``user@host:path``, a path, or ends in ``.git``.

    Such an ident is never looked up by name: :func:`typed_repo` takes it, and
    refuses the paths.
    """
    return (
        "://" in ident
        or ident.startswith(("/", "~"))
        or ident.endswith(".git")
        or repo_url_parts(ident)[0] is not None
    )


@experimental
def typed_repo(url: str) -> Repo:
    """The repo a typed URL names: a plain :class:`Repo` called by the URL's full path.

    No provider is asked; credentials still come from whoever fills
    ``GitHost`` for the URL's host. A URL untaped refuses (a path,
    ``file://``, credentials in it) is a :class:`UsageError`.
    """
    _, segments = repo_url_parts(url)
    name = "/".join(segments) or url
    try:
        return Repo(name=name, url=url)
    except ValidationError as exc:
        raise UsageError(
            f"repo {q(url)}: {first_validation_error(exc)}",
            hint="pass an https:// or ssh:// clone URL, or a repo name",
        ) from None
