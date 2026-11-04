"""A source's repos: what one ``RepoSource`` provider selects for a saved ``RepoSelector``.

A plugin that keeps a selector (a saved source, say) never reads it: it hands
it back to the provider that issued it with :func:`select_repos`. A selector
carrying the provider's own record asks ``RepoSource.select``; one without
(``match`` patterns only), or one whose provider doesn't fill ``select``,
takes the provider's ``repos()``. Either way ``match`` narrows the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from untaped.sdk import UntapedError, UsageError, experimental
from untaped.sdk.contracts import Answers, Errored, Ok, Reason, Skipped, gather
from untaped_git.repos import Repo, RepoSelector, RepoSource


@experimental
@dataclass(frozen=True, slots=True)
class Selection:
    """The repos a selector takes; ``note`` says why ``repos()`` stood in for ``select``."""

    repos: tuple[Repo, ...]
    note: str | None = None


@experimental
def select_repos(plugin: str, selector: RepoSelector, *, refresh: bool | None = None) -> Selection:
    """The repos of ``plugin`` that ``selector`` takes.

    ``refresh`` directs the answer cache as :func:`~untaped.sdk.contracts.gather`
    does; with ``True`` a live call that failed raises even when a stored
    answer could stand in. A provider's failure raises with its own category,
    a provider that isn't ready raises ``NoProviderReady`` (exit 4), and a
    selector another plugin issued is a :class:`UsageError`.
    """
    issued = selector.source
    if issued is not None and issued.plugin != plugin:
        raise UsageError(
            f"a selector {issued.plugin} issued can't select {plugin}'s repos",
            hint=f"pipe a selector {plugin} issues",
        )
    note = None
    if issued is not None:
        asked = gather(RepoSource.select, plugins={plugin}, refresh=refresh)
        selected = _one(asked(selector), live=refresh is True)
        if not isinstance(selected, Skipped):
            return Selection(_matching(selected, selector.match))
        note = f"{selected.detail}: took every repo it lists" + _matched(selector.match)
    listing = gather(RepoSource.repos, plugins={plugin}, refresh=refresh)
    listed = _one(listing(), live=refresh is True)
    if isinstance(listed, Skipped):
        raise UntapedError(f"{plugin} lists no repos: {listed.detail}", category="config")
    return Selection(_matching(listed, selector.match), note)


def match_name(name: str, patterns: list[str]) -> bool:
    """Whether a repo's full ``name`` matches any glob in ``patterns`` (any, when none); no case."""
    lowered = name.lower()
    return not patterns or any(fnmatchcase(lowered, pattern.lower()) for pattern in patterns)


def _matching(repos: list[Repo], patterns: list[str]) -> tuple[Repo, ...]:
    return tuple(repo for repo in repos if match_name(repo.name, patterns))


def _matched(patterns: list[str]) -> str:
    return f" matching {', '.join(patterns)}" if patterns else ""


def _one(answers: Answers[list[Repo]], *, live: bool) -> list[Repo] | Skipped:
    """The one asked provider's repos; its failure raises, and a ``not-filled`` skip returns.

    Any other skip (no cached answer) raises. Under ``live``, a stored answer
    standing in for a failed live call raises that call's error instead.
    """
    if live:
        answers.take_stale()  # its error raises below instead
    for answer in answers:
        if isinstance(answer, Errored):
            raise answer.error
        if isinstance(answer, Ok):
            if live and answer.stale is not None:
                raise answer.stale.error
            return answer.value
        if answer.reason is not Reason.not_filled:
            raise UntapedError(answer.detail, category="config")
        return answer
    raise AssertionError("gather(plugins=) answered for no plugin")  # pragma: no cover
