"""Resolve what the user typed to a :class:`Repo`, asking every plugin that fills ``RepoSource``.

A typed URL becomes a plain repo named by its full path and no provider is
asked. A name is matched (:func:`workspace_match`) over every ready
provider's ``repos()`` with the SDK's ``select_one``: a stale listing confirms
a match, never an absence, and two unranked providers listing it are
ambiguous until ``untaped plugin rank`` orders them. Every repo, typed,
listed, picked or piped, passes the provenance check before anything is
fetched.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence

from untaped.contracts import Answers, NoProviderReady, NotFound, Ok, Skipped, gather, select_one
from untaped.sdk import UsageError, not_found, q, repo_url_parts
from untaped_git.api import GitHost
from untaped_workspace.api import Repo, RepoSource
from untaped_workspace.domain.naming import looks_like_url, typed_repo, workspace_match

_URL_HINT = "pass the repo's clone URL"


class RepoSources:
    """``RepoCatalog`` over the plugins filling ``RepoSource`` (asked once per catalog)."""

    def __init__(self) -> None:
        self._answers: Answers[list[Repo]] | None = None

    def resolve(self, ident: str) -> Repo:
        """The repo ``ident`` names; ``UsageError`` (exit 2) when it is unknown or ambiguous.

        A provider's own failure that could hide the match raises with its
        own category and exit code (an expired token is never "not found").
        """
        if looks_like_url(ident):
            return self.admit(typed_repo(ident))
        try:
            answers = self._ask()
        except NoProviderReady as exc:
            raise UsageError(not_found("repo", ident), hint=_unasked_hint(exc)) from None
        try:
            repo = select_one(answers, lambda repo: workspace_match(repo.name, ident))
        except NotFound as exc:
            raise UsageError(_not_found(ident, answers), hint=exc.hint or _URL_HINT) from None
        return self.admit(repo)

    def admit(self, repo: Repo) -> Repo:
        """``repo``, unless its source vouches for another host than its URL's (exit 2).

        A source plugin that fills the git plugin's ``GitHost`` for one host
        never issues a URL on another; one that fills no ``GitHost`` (or a
        typed URL, with no source) is not checked.
        """
        source = repo.source
        if source is None:
            return repo
        try:
            homes = gather(GitHost.home, plugins={source.plugin})()
        except NoProviderReady:
            return repo
        host, _ = repo_url_parts(repo.url)
        for answer in homes:
            if isinstance(answer, Ok) and answer.value and answer.value.lower() != host:
                raise UsageError(
                    f"repo {q(repo.name)} from {source.plugin} has a URL on {host or 'no host'}, "
                    f"but {source.plugin} serves {answer.value}: refusing to fetch it",
                    hint=f"pass the clone URL yourself if you trust {repo.url}",
                )
        return repo

    def reask(self, repo: Repo) -> Repo | None:
        """``repo`` as its source plugin lists it now (a live call), admitted.

        ``None`` when nobody can be asked: a typed URL (no source), or a
        source plugin that is uninstalled or not ready in this profile.
        """
        source = repo.source
        if source is None:
            return None
        try:
            answers = gather(RepoSource.repos, refresh=True, plugins={source.plugin})()
        except NoProviderReady:
            return None
        return self.admit(select_one(answers, lambda listed: listed.name == repo.name))

    def _ask(self) -> Answers[list[Repo]]:
        if self._answers is None:
            self._answers = gather(RepoSource.repos)()
        return self._answers


def _unasked_hint(exc: NoProviderReady) -> str:
    """``github wasn't asked: …; set github.default_org, or pass the repo's clone URL``."""
    if not exc.not_ready:
        return f"no installed plugin lists repos; {_URL_HINT}"
    parts = []
    for plugin, ready in exc.not_ready.items():
        setting = f"; set {ready.setting}" if ready.setting else ""
        parts.append(f"{plugin} wasn't asked: {ready.reason}{setting}")
    return f"{'; '.join(parts)}, or {_URL_HINT}"


def _not_found(ident: str, answers: Answers[list[Repo]]) -> str:
    """``repo not found: 'x'``, naming who wasn't asked and the close matches among every name."""
    message = not_found("repo", ident)
    unasked = [f"{a.plugin} ({a.reason})" for a in answers if isinstance(a, Skipped)]
    if unasked:
        message += f"; not asked: {', '.join(unasked)}"
    names = [repo.name for answer in answers if isinstance(answer, Ok) for repo in answer.value]
    if suggestions := _close(ident, names):
        message += f"; did you mean {', '.join(map(q, suggestions))}?"
    return message


def _close(ident: str, names: Sequence[str]) -> list[str]:
    by_key: dict[str, list[str]] = {}
    for name in names:
        key = name if "/" in ident else name.rpartition("/")[2]
        by_key.setdefault(key.lower(), []).append(name)
    close = difflib.get_close_matches(ident.lower(), list(by_key), n=3)
    return sorted({name for key in close for name in by_key[key]})
