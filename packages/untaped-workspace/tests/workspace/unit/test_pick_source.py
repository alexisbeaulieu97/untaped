"""The picker's catalog: every ``RepoSource`` provider's answer, then repos only the store has."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from untaped.contracts import Answers, Failed, NoProviderReady, NotReady, Ok, Skipped, Source
from untaped.sdk import UntapedError
from untaped.testing import compose_with
from untaped_git import SPEC as GIT
from untaped_workspace import SPEC as WORKSPACE
from untaped_workspace.api import Repo
from untaped_workspace.domain import RepoArg, StoredRepo, looks_like_url, repo_key
from untaped_workspace.infrastructure.pick_source import RepoPickSource
from workspace.fakes import Listing, age_answers, repo

NOW = datetime.now(UTC)
API = repo("acme/api", description="Core API")
OLD = repo("acme/old", archived=True)


def _stored(ident: str, origin: str | None = None) -> StoredRepo:
    """The store repo of ``ident`` (``host/[owner/]name``), first fetched from ``origin``."""
    *parents, name = ident.split("/")
    return StoredRepo(key=(*parents, f"{name}.git"), origin=origin or f"https://{ident}")


class FakeGit:
    def __init__(self, *stored: StoredRepo) -> None:
        self.calls: list[str] = []
        self._stored = sorted(stored, key=lambda stored: stored.ident)

    def remote_branches(self, url: str) -> list[str]:
        self.calls.append(url)
        return ["main", "release/2"]

    def stored_repos(self) -> list[StoredRepo]:
        return self._stored


def answers(*items: Ok[list[Repo]] | Failed | Skipped) -> Answers[list[Repo]]:
    return Answers(items, owner="workspace", contract="repo_source", method="repos")


def ok(plugin: str, *repos: Repo, **fields: object) -> Ok[list[Repo]]:
    """``plugin``'s answer listing ``repos``, each stamped with ``plugin`` as its source."""
    source = Source(plugin=plugin, kind="workspace.repo")
    stamped = [r.model_copy(update={"source": source}) for r in repos]
    return Ok(plugin, stamped, **fields)  # type: ignore[arg-type]


type Ask = Callable[[bool | None], Answers[list[Repo]]]


def always(*items: Ok[list[Repo]] | Failed | Skipped) -> Ask:
    return lambda refresh: answers(*items)


def _source(git: FakeGit | None = None, *, ask: Ask, **kwargs: object) -> RepoPickSource:
    return RepoPickSource(git=git or FakeGit(), ask=ask, **kwargs)  # type: ignore[arg-type]


def test_provider_repos_then_stored_only_repos_with_a_footer_per_provider() -> None:
    """S7 (first pass): each provider's count and age, then the repos only the store has."""
    git = FakeGit(_stored("github.com/acme/api"), _stored("github.com/team/tool"))
    ask = always(
        ok("github", API, OLD, refreshed_at=NOW - timedelta(hours=3, minutes=5)),
        ok("gitlab", repo("infra/web", "gitlab.example")),
    )
    catalog = _source(git, ask=ask).catalog(refresh=False)
    assert [(i.id, i.dimmed, i.description) for i in catalog.items] == [
        ("acme/api", False, "Core API"),
        ("acme/old", True, ""),
        ("infra/web", False, ""),
        ("github.com/team/tool", False, "repo store"),
    ]
    assert catalog.note == "github: 2 repos, refreshed 3h ago · gitlab: 1 repo · 1 stored only"


def test_the_refresh_directive_reaches_the_providers() -> None:
    asked: list[bool | None] = []

    def ask(refresh: bool | None) -> Answers[list[Repo]]:
        asked.append(refresh)
        return answers(ok("github", API))

    source = _source(ask=ask)
    for refresh in (False, None, True):
        source.catalog(refresh=refresh)
    assert asked == [False, None, True]


def test_a_provider_serving_its_stored_answer_says_so_and_a_pick_of_another_works() -> None:
    """S7 (after the refresh): github down, its stored answer stands in; a gitlab pick works."""
    down = Failed("github", UntapedError("HTTP 503"))
    infra = repo("infra/web", "gitlab.example")
    source = _source(ask=always(ok("github", API, stale=down), ok("gitlab", infra)))
    catalog = source.catalog(refresh=True)
    assert catalog.note == "github: cached repos only: HTTP 503 · gitlab: 1 repo"
    picked = source.pick_arg(RepoArg(ident="infra/web", read_only=True))
    assert picked.repo is not None and picked.repo.source is not None
    assert (picked.ident, picked.read_only, picked.repo.url, picked.repo.source.plugin) == (
        "infra/web",
        True,
        "https://gitlab.example/infra/web.git",
        "gitlab",
    )


def test_an_answer_with_invalid_rows_counts_them_and_names_the_upgrade() -> None:
    """S8: ``gitlab: 1 repo, 1 invalid (…; upgrade untaped-gitlab)``."""
    invalid = ("row 2: name: String should match pattern '^[^/\\s]+(/[^/\\s]+)*$'",)
    ask = always(ok("gitlab", repo("infra/web", "gitlab.example"), invalid=invalid))
    assert _source(ask=ask).catalog(refresh=False).note == (
        f"gitlab: 1 repo, 1 invalid ({invalid[0]}; upgrade untaped-gitlab)"
    )


@pytest.mark.parametrize(
    ("answer", "note"),
    [
        (Failed("gitlab", UntapedError("401 Unauthorized")), "gitlab: 401 Unauthorized"),
        (Skipped("gitlab", "not-configured", "no base URL"), ""),
        (Skipped("gitlab", "no-cache", "no cached answer is stored"), "gitlab: not listed yet"),
        (Skipped("gitlab", "missing-bridge", "no to_repo"), "gitlab: missing-bridge (no to_repo)"),
    ],
)
def test_a_provider_without_repos_says_why(answer: Failed | Skipped, note: str) -> None:
    assert _source(ask=always(answer)).catalog(refresh=None).note == note


def test_with_no_provider_ready_the_store_alone_is_offered() -> None:
    def nobody(refresh: bool | None) -> Answers[list[Repo]]:
        raise NoProviderReady(
            "no provider of workspace.repo_source.repos is ready: github: no inventory scope",
            not_ready={"github": NotReady("no inventory scope")},
        )

    source = _source(FakeGit(_stored("github.com/team/tool")), ask=nobody)
    catalog = source.catalog(refresh=None)
    assert [i.id for i in catalog.items] == ["github.com/team/tool"]
    assert catalog.note == (
        "stored repos only — no provider of workspace.repo_source.repos is ready: "
        "github: no inventory scope · 1 stored only"
    )


def test_a_name_two_providers_list_is_one_item_per_provider() -> None:
    gitlab_api = repo("acme/api", "gitlab.example")
    source = _source(ask=always(ok("github", API), ok("gitlab", gitlab_api)))
    items = source.catalog(refresh=False).items
    assert [(i.id, i.label) for i in items] == [
        ("acme/api (github)", "acme/api (github)"),
        ("acme/api (gitlab)", "acme/api (gitlab)"),
    ]
    picked = source.pick_arg(RepoArg(ident="acme/api (gitlab)"))
    assert picked.ident == "acme/api"
    assert picked.repo is not None and picked.repo.url == gitlab_api.url


def test_excluded_repos_are_not_offered() -> None:
    source = _source(
        FakeGit(_stored("github.com/team/tool")),
        ask=always(ok("github", API, OLD)),
        exclude={
            repo_key("git@github.com:acme/api.git"),
            repo_key("https://github.com/team/tool"),
        },
    )
    assert [i.id for i in source.catalog(refresh=False).items] == ["acme/old"]


def test_branch_completion_reads_the_store_once_per_item() -> None:
    git = FakeGit()
    source = _source(git, ask=always(ok("github", API)))
    source.catalog(refresh=False)
    for _ in range(5):
        assert source.branches("acme/api") == ["main", "release/2"]
    assert git.calls == ["https://github.com/acme/api.git"]
    assert source.branches(None) == []


def test_branches_before_the_catalog_is_not_memoised() -> None:
    git = FakeGit(_stored("github.com/team/tool"))
    source = _source(git, ask=always())
    assert source.branches("github.com/team/tool") == []
    source.catalog(refresh=False)
    assert source.branches("github.com/team/tool") == ["main", "release/2"]
    assert git.calls == ["https://github.com/team/tool"]


def test_a_stored_only_pick_is_a_plain_repo_of_its_stored_origin() -> None:
    git = FakeGit(_stored("gitlab.example/team/tool", "git@gitlab.example:team/tool.git"))
    source = _source(git, ask=always(Failed("github", UntapedError("HTTP 503"))))
    source.catalog(refresh=None)
    assert source.pick_arg(RepoArg(ident="gitlab.example/team/tool")) == RepoArg(
        ident="gitlab.example/team/tool",
        repo=Repo(name="gitlab.example/team/tool", url="git@gitlab.example:team/tool.git"),
    )


def test_a_stored_origin_untaped_no_longer_accepts_is_left_out() -> None:
    """A store repo first fetched from a local path (before ``GitUrl``) is no remote."""
    git = FakeGit(_stored("srv/team/tool", "/srv/team/tool.git"), _stored("github.com/acme/web"))
    items = _source(git, ask=always()).catalog(refresh=False).items
    assert [i.id for i in items] == ["github.com/acme/web"]


def test_a_typed_url_pick_keeps_its_ident() -> None:
    source = _source(ask=always(ok("github", API)))
    source.catalog(refresh=False)
    assert source.pick_arg(RepoArg(ident="https://h.example/o/r")) == RepoArg(
        ident="https://h.example/o/r"
    )


def test_a_pick_dropped_by_a_refresh_keeps_its_repo() -> None:
    loads = iter([answers(ok("github", API, OLD)), answers(ok("github", OLD))])
    source = _source(ask=lambda refresh: next(loads))
    source.catalog(refresh=False)
    assert [i.id for i in source.catalog(refresh=True).items] == ["acme/old"]
    picked = source.pick_arg(RepoArg(ident="acme/api"))
    assert picked.repo is not None and picked.repo.url == API.url


def test_a_listed_repo_spelled_in_another_case_is_listed_once() -> None:
    git = FakeGit(_stored("github.com/acme/api"))
    listed = Repo(name="Acme/API", url="https://github.com/Acme/API")
    items = _source(git, ask=always(ok("github", listed))).catalog(refresh=False).items
    assert [i.id for i in items] == ["Acme/API"]


def test_one_repo_stored_from_two_hosts_is_two_items() -> None:
    git = FakeGit(_stored("github.com/team/tool"), _stored("gitlab.example/team/tool"))
    items = _source(git, ask=always()).catalog(refresh=False).items
    assert [(i.id, i.label) for i in items] == [
        ("github.com/team/tool", "github.com/team/tool"),
        ("gitlab.example/team/tool", "gitlab.example/team/tool"),
    ]


def test_an_owner_less_hosted_repo_is_offered() -> None:
    git = FakeGit(_stored("git.example/project", "https://git.example/project.git"))
    items = _source(git, ask=always()).catalog(refresh=False).items
    assert [i.id for i in items] == ["git.example/project"]
    assert repo_key("https://git.example/project.git") == ("git.example", "project.git")


def test_a_stored_id_never_shadows_a_listed_id() -> None:
    """An owner-less repo on a dotless host (``acme/api.git``) has the id ``acme/api``."""
    git = FakeGit(_stored("acme/api", "http://acme/api.git"))
    source = _source(git, ask=always(ok("github", API, OLD)))
    ids = [item.id for item in source.catalog(refresh=False).items]
    assert ids == ["acme/api", "acme/old"]
    picked = source.pick_arg(RepoArg(ident="acme/api"))
    assert picked.repo is not None and picked.repo.url == API.url


@pytest.mark.parametrize(
    ("ident", "expected"),
    [
        ("git@host:o/r.git", True),
        ("git@host:o/r", True),
        ("https://h/o/r", True),
        ("/srv/r.git", True),
        ("~/r", True),
        ("api.git", True),
        ("./x", False),
        ("acme/api", False),
        ("api", False),
    ],
)
def test_looks_like_url(ident: str, expected: bool) -> None:
    assert looks_like_url(ident) is expected


def test_the_real_providers_first_pass_serves_stored_answers_then_the_refresh_asks() -> None:
    """S7 end to end over ``gather``: nothing stored is "not listed yet" (no network on the
    first pass); after a refresh github's stored answer stands in for its failed call."""
    github = Listing(API)
    gitlab = Listing(repo("infra/web", "gitlab.example"))
    with compose_with(GIT, WORKSPACE, provides={"github": [github], "gitlab": [gitlab]}):
        source = RepoPickSource(git=FakeGit())  # type: ignore[arg-type]
        first = source.catalog(refresh=False)
        assert first.note == "github: not listed yet · gitlab: not listed yet"
        assert (github.calls, gitlab.calls) == (0, 0)
        assert source.catalog(refresh=True).note.startswith("github: 1 repo, refreshed just now")
        age_answers(timedelta(hours=3))
        github.error = UntapedError("HTTP 503", category="unavailable")
        cached = source.catalog(refresh=False)
        assert cached.note == "github: 1 repo, refreshed 3h ago · gitlab: 1 repo, refreshed 3h ago"
        refreshed = source.catalog(refresh=True)
        assert refreshed.note == (
            "github: cached repos only: HTTP 503 · gitlab: 1 repo, refreshed just now"
        )
        assert [i.id for i in refreshed.items] == ["acme/api", "infra/web"]
        picked = source.pick_arg(RepoArg(ident="infra/web"))
        assert picked.repo is not None and picked.repo.source is not None
        assert picked.repo.source.plugin == "gitlab"
