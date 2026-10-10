"""The repo picker's catalog: every ``RepoSource`` provider's repos plus repos already in the store.

The first pass asks with ``refresh=False`` (cached answers only, no network),
then the picker refreshes. Repos workspace has used that only the repo store
holds (:meth:`GitWorktrees.stored_repos`) are added, so the picker also works
offline or with no provider configured. The footer says, per provider, how
many repos it listed and how fresh they are. Branch completion reads the
store only, never the network.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError

from untaped.contracts import Answers, Failed, NoProviderReady, Ok, Skipped, gather
from untaped.sdk import PickCatalog, PickItem
from untaped_workspace.api import Repo, RepoSource
from untaped_workspace.domain.models import RepoArg
from untaped_workspace.domain.naming import repo_key

if TYPE_CHECKING:
    from untaped_workspace.application.ports import GitWorktrees

type _Key = tuple[str, ...]
type Ask = Callable[[bool | None], Answers[list[Repo]]]


def _ask(refresh: bool | None) -> Answers[list[Repo]]:
    return gather(RepoSource.repos, refresh=refresh)()


def _age(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _repos(count: int) -> str:
    return f"{count:,} repo{'' if count == 1 else 's'}"


class RepoPickSource:
    """Picker items from the repo providers and the repo store, plus branch completion.

    ``exclude`` holds the repo keys (:func:`repo_key`) of repos the workspace
    already has; they are not offered. ``ask`` stands in for the providers
    (tests).
    """

    def __init__(
        self,
        *,
        git: GitWorktrees,
        ask: Ask | None = None,
        exclude: Collection[_Key] = (),
    ) -> None:
        self._git = git
        self._ask = ask or _ask
        self._exclude = frozenset(exclude)
        self._seen: dict[str, Repo] = {}
        """Item id -> repo across loads (never cleared): a pick a refresh dropped keeps its repo."""
        self._branches: dict[str, list[str]] = {}

    def catalog(self, *, refresh: bool | None) -> PickCatalog:
        """Every provider's repos then stored-only ones; the footer says how fresh each is."""
        answers: Answers[list[Repo]] | None
        try:
            answers = self._ask(refresh)
        except NoProviderReady as exc:
            answers, notes = None, [f"stored repos only — {exc}"]
        else:
            notes = [note for answer in answers if (note := _note(answer))]
        listed, known = self._listed(answers)
        stored = self._stored(known, taken={item.id for item, _ in listed})
        if stored:
            notes.append(f"{len(stored)} stored only")
        self._seen.update({item.id: repo for item, repo in (*listed, *stored)})
        return PickCatalog(
            items=tuple(item for item, _ in (*listed, *stored)), note=" · ".join(notes)
        )

    def pick_arg(self, arg: RepoArg) -> RepoArg:
        """``arg`` (its ident a picked item id) with the picked repo, so nothing is looked up again.

        A typed URL (no listed repo) keeps its ident and is resolved as typed.
        """
        repo = self._seen.get(arg.ident)
        if repo is None:
            return arg
        return arg.model_copy(update={"ident": repo.name, "repo": repo})

    def branches(self, item_id: str | None) -> list[str]:
        """Stored remote branches of ``item_id`` (no network); ``[]`` for the all-items row."""
        if item_id is None:
            return []
        if item_id not in self._branches:
            repo = self._seen.get(item_id)
            self._branches[item_id] = [] if repo is None else self._git.remote_branches(repo.url)
        return self._branches[item_id]

    # -- helpers -----------------------------------------------------------

    def _listed(
        self, answers: Answers[list[Repo]] | None
    ) -> tuple[list[tuple[PickItem, Repo]], set[_Key]]:
        """The offered repos of every answer (rank order) and the store keys of every listed one.

        A name two providers list is shown once per provider, its id and
        label suffixed with the provider's name (``acme/api (gitlab)``).
        """
        oks = [answer for answer in answers or () if isinstance(answer, Ok)]
        names = Counter(repo.name for ok in oks for repo in {r.name: r for r in ok.value}.values())
        items: list[tuple[PickItem, Repo]] = []
        known: set[_Key] = set()
        for ok in oks:
            for repo in ok.value:
                key = repo_key(repo.url)
                known.add(key)
                if key in self._exclude:
                    continue
                ident = repo.name if names[repo.name] == 1 else f"{repo.name} ({ok.plugin})"
                item = PickItem(
                    id=ident, label=ident, description=repo.description or "", dimmed=repo.archived
                )
                items.append((item, repo))
        return items, known

    def _stored(
        self, known: Collection[_Key], *, taken: Collection[str]
    ) -> list[tuple[PickItem, Repo]]:
        """Stored repos neither listed (``known``) nor excluded, as plain repos of their URL.

        A stored id in ``taken`` (the listed ids) is skipped: an owner-less
        repo on a dotless host (``acme/api.git``) would otherwise shadow a
        provider's ``acme/api``. A stored URL untaped no longer accepts as a
        remote (a local path) is left out.
        """
        items: list[tuple[PickItem, Repo]] = []
        for stored in self._git.stored_repos():
            if stored.key in known or stored.key in self._exclude or stored.ident in taken:
                continue
            try:
                repo = Repo(name=stored.ident, url=stored.origin)
            except ValidationError:
                continue
            item = PickItem(id=stored.ident, label=stored.ident, description="repo store")
            items.append((item, repo))
        return items


def _note(answer: Ok[list[Repo]] | Failed | Skipped) -> str | None:
    """One provider's footer part: how many repos and how fresh, or why there are none."""
    plugin = answer.plugin
    match answer:
        case Ok(stale=Failed(error=error)):
            return f"{plugin}: cached repos only: {error}"
        case Ok(value=value, refreshed_at=at, invalid=invalid):
            note = f"{plugin}: {_repos(len(value))}"
            if at is not None:
                note += f", refreshed {_age(datetime.now(UTC) - at)} ago"
            if invalid:
                note += f", {len(invalid)} invalid ({invalid[0]}; upgrade untaped-{plugin})"
            return note
        case Failed(error=error):
            return f"{plugin}: {error}"
        case Skipped(reason="not-configured"):
            return None
        case Skipped(reason="no-cache"):
            return f"{plugin}: not listed yet"
        case Skipped(reason=reason, detail=detail):
            return f"{plugin}: {reason} ({detail})"
