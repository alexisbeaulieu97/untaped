"""``select_repos``: a saved selector handed back to the provider that issued it.

Providers here are composed as plugins (``gitlab``, ``forge``) the way a saved
source's provider is: a selector piped through ``RepoSource.to_selector`` carries
its provider's own record, and ``select_repos`` asks that provider for it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

import pytest

from git_test_support import composed
from untaped.sdk import ExitCode, Record, UntapedError, UsageError
from untaped.sdk.contracts import NoProviderReady, Provider
from untaped.sdk.testing import ManualClock, piped_through
from untaped_git.api import Repo, RepoSelector, RepoSource, select_repos
from untaped_git.selection import match_name
from untaped_git.testing import conformance, listing, make_repo

GROUPS = {
    "infra": [make_repo("infra/api", "gitlab.example"), make_repo("infra/web", "gitlab.example")],
    "data": [make_repo("data/lake", "gitlab.example")],
}


class GroupSelector(Record):
    """A forge's own selector: the groups whose repos to take."""

    groups: list[str]


class Groups(RepoSource, Provider):
    """Selects the repos of its groups; ``repos()`` lists every group's."""

    asked: ClassVar[list[list[str]]] = []
    error: ClassVar[UntapedError | None] = None

    def to_selector(self, item: GroupSelector) -> RepoSelector:
        return RepoSelector()

    def select(self, selector: RepoSelector) -> list[Repo]:
        groups = self.own(selector, GroupSelector).groups
        type(self).asked.append(groups)
        if type(self).error is not None:
            raise type(self).error
        return [repo for group in groups for repo in GROUPS[group]]

    def repos(self) -> list[Repo]:
        return [repo for repos in GROUPS.values() for repo in repos]


class Unread(RepoSource, Provider):
    """Issues selectors but can't read them back: ``select`` is not filled."""

    def to_selector(self, item: GroupSelector) -> RepoSelector:
        return RepoSelector()

    def repos(self) -> list[Repo]:
        return [make_repo("infra/api", "gitlab.example"), make_repo("data/lake", "gitlab.example")]


@pytest.fixture(autouse=True)
def _reset() -> None:
    Groups.asked = []
    Groups.error = None


def selector(*groups: str, match: list[str] | None = None) -> RepoSelector:
    issued = piped_through(RepoSource.to_selector, GroupSelector(groups=list(groups)))
    return issued.model_copy(update={"match": match or []})


def names(repos: tuple[Repo, ...]) -> list[str]:
    return [repo.name for repo in repos]


def test_a_providers_selector_is_read_back_by_its_select() -> None:
    with composed({"gitlab": (Groups,)}):
        selection = select_repos("gitlab", selector("infra"))

    assert names(selection.repos) == ["infra/api", "infra/web"]
    assert selection.note is None
    assert Groups.asked == [["infra"]]


def test_match_narrows_what_select_answers() -> None:
    with composed({"gitlab": (Groups,)}):
        selection = select_repos("gitlab", selector("infra", match=["*/WEB"]))

    assert names(selection.repos) == ["infra/web"]


def test_a_match_only_selector_takes_the_listing_by_name() -> None:
    gitlab = listing(make_repo("infra/api"), make_repo("Infra/Web"), make_repo("data/lake"))
    with composed({"gitlab": (gitlab,)}):
        selection = select_repos("gitlab", RepoSelector(match=["infra/*"]))

    assert names(selection.repos) == ["infra/api", "Infra/Web"]
    assert selection.note is None


def test_an_empty_selector_takes_every_listed_repo() -> None:
    gitlab = listing(make_repo("infra/api"), make_repo("data/lake"))
    with composed({"gitlab": (gitlab,)}):
        selection = select_repos("gitlab", RepoSelector())

    assert names(selection.repos) == ["infra/api", "data/lake"]


def test_a_provider_without_select_is_listed_and_says_so() -> None:
    with composed({"gitlab": (Unread,)}):
        issued = piped_through(RepoSource.to_selector, GroupSelector(groups=["infra"]))
        selection = select_repos("gitlab", issued.model_copy(update={"match": ["infra/*"]}))

    assert names(selection.repos) == ["infra/api"]
    assert selection.note == (
        "gitlab doesn't fill RepoSource.select: took every repo it lists matching infra/*"
    )


def test_a_selector_another_plugin_issued_is_refused() -> None:
    with composed({"gitlab": (Groups,), "forge": (listing(make_repo("a/b")),)}):
        issued = selector("infra")
        with pytest.raises(UsageError, match="a selector gitlab issued can't select forge's"):
            select_repos("forge", issued)


def test_a_providers_failure_raises_with_its_own_category() -> None:
    Groups.error = UntapedError("token rejected", category="auth")
    with composed({"gitlab": (Groups,)}), pytest.raises(UntapedError) as caught:
        select_repos("gitlab", selector("infra"))

    assert caught.value.category == "auth"


def test_a_provider_that_is_not_installed_is_not_ready() -> None:
    with composed({"gitlab": (Groups,)}), pytest.raises(NoProviderReady) as caught:
        select_repos("forge", RepoSelector())

    assert caught.value.exit_code == ExitCode.ENVIRONMENT


def test_selections_are_kept_per_selector(clock: ManualClock) -> None:
    with composed({"gitlab": (Groups,)}):
        select_repos("gitlab", selector("infra"))
        select_repos("gitlab", selector("infra"))
        select_repos("gitlab", selector("data"))
        clock.advance(timedelta(hours=7))
        select_repos("gitlab", selector("infra"))

    assert Groups.asked == [["infra"], ["data"], ["infra"]]


def test_match_does_not_key_the_kept_selection() -> None:
    with composed({"gitlab": (Groups,)}):
        api = select_repos("gitlab", selector("infra", match=["*/api"]))
        web = select_repos("gitlab", selector("infra", match=["*/web"]))

    assert (names(api.repos), names(web.repos)) == (["infra/api"], ["infra/web"])
    assert Groups.asked == [["infra"]]


def test_a_refresh_asks_live_and_a_failed_live_call_raises(clock: ManualClock) -> None:
    with composed({"gitlab": (Groups,)}):
        select_repos("gitlab", selector("infra"))
        select_repos("gitlab", selector("infra"), refresh=True)
        assert Groups.asked == [["infra"], ["infra"]]

        Groups.error = UntapedError("gitlab is down", category="unavailable")
        with pytest.raises(UntapedError, match="gitlab is down"):
            select_repos("gitlab", selector("infra"), refresh=True)


@pytest.mark.parametrize(
    ("name", "patterns", "expected"),
    [
        ("acme/api", [], True),
        ("acme/api", ["acme/*"], True),
        ("ACME/Api", ["acme/api"], True),
        ("infra/platform/base", ["infra/*"], True),
        ("acme/api", ["other/*", "*/api"], True),
        ("acme/api", ["acme"], False),
    ],
)
def test_match_name(name: str, patterns: list[str], expected: bool) -> None:
    assert match_name(name, patterns) is expected


def test_a_provider_issuing_selectors_without_select_does_not_conform() -> None:
    with pytest.raises(AssertionError, match="fills select to read them back"):
        conformance(Unread())


def test_a_provider_issuing_and_reading_selectors_conforms() -> None:
    conformance(Groups())
