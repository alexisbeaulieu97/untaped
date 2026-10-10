"""``GithubRepos``: GitHub fills workspace's ``RepoSource`` with its repository inventory.

Asked the way workspace asks it: ``gather`` for the listing and readiness,
``convert`` for a piped ``github.repo`` row. The provider's scope helper
``inventory_scope`` is tested directly.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from untaped.contracts import Failed, NoProviderReady, Ok, convert, gather
from untaped.sdk import ExitCode, PipeEnvelope, UntapedError, UsageError
from untaped.settings import get_settings
from untaped.testing import assert_fills, compose_with
from untaped_github.domain.models import GithubRepo
from untaped_github.providers.workspace import GithubRepos, inventory_scope
from untaped_github.settings import GithubSettings
from untaped_workspace.api import Repo, RepoSource

API = "https://api.github.com"

ACME = [
    {
        "id": 1,
        "full_name": "acme/api",
        "html_url": "https://github.com/acme/api",
        "url": "https://api.github.com/repos/acme/api",
        "clone_url": "https://github.com/acme/api.git",
        "ssh_url": "git@github.com:acme/api.git",
        "default_branch": "main",
        "description": "Core REST API",
        "archived": False,
        "pushed_at": "2026-09-30T10:00:00Z",
    },
    {
        "id": 2,
        "full_name": "acme/web",
        "html_url": "https://github.com/acme/web",
        "clone_url": "https://github.com/acme/web.git",
        "ssh_url": "git@github.com:acme/web.git",
        "default_branch": "trunk",
        "archived": True,
    },
]

#: Rows as github commands emit them: a full listing, a search hit (no clone
#: URLs) and a sparse row (only ``full_name``).
SAMPLES: list[object] = [
    GithubRepo.model_validate(ACME[0]),
    GithubRepo(
        full_name="acme/web",
        html_url="https://github.com/acme/web",
        description="Website",
        language="TypeScript",
        stargazers_count=12,
        forks_count=3,
        private=False,
        fork=False,
        updated_at="2026-09-01T08:00:00Z",
    ),
    {"full_name": "acme/sparse"},
]


def configure(**github: object) -> None:
    """Write the default profile's ``github`` section (with a token) and forget cached settings."""
    path = Path(os.environ["UNTAPED_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    section = {"token": "ghp_test", **github}
    path.write_text(yaml.safe_dump({"profiles": {"default": {"github": section}}}))
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _composed() -> Iterator[None]:
    with compose_with("git", "github", "workspace"):
        yield


def piped(record: dict[str, object], kind: str = "github.repo") -> Repo:
    """A piped row read the way workspace reads one: through the contract's bridge."""
    return convert(RepoSource.to_repo, PipeEnvelope(kind=kind, record=record, lineno=1))


def listed() -> Ok[list[Repo]]:
    [answer] = gather(RepoSource.repos, refresh=True)()
    assert isinstance(answer, Ok), answer
    return answer


# --- fills ---------------------------------------------------------------------------------


def test_github_repos_fills_repo_source() -> None:
    configure(default_org="acme")

    assert_fills(GithubRepos, samples=SAMPLES)


# --- ready -----------------------------------------------------------------------------------


def test_without_an_inventory_scope_github_is_not_ready_and_names_the_setting() -> None:
    # S12: workspace says which setting to set instead of guessing a URL.
    configure()

    with pytest.raises(NoProviderReady) as caught:
        gather(RepoSource.repos, refresh=True)()

    not_ready = caught.value.not_ready["github"]
    assert not_ready.setting == "github.default_org"
    assert "no scope" in not_ready.reason
    assert caught.value.exit_code == ExitCode.ENVIRONMENT


@pytest.mark.parametrize(
    "github",
    [{"default_org": "acme"}, {"inventory": {"orgs": ["acme"]}}],
    ids=["default-org", "inventory-orgs"],
)
def test_a_configured_scope_makes_github_ready(github: dict[str, object]) -> None:
    configure(**github)

    with respx.mock(base_url=API) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        answer = listed()

    assert answer.plugin == "github"


# --- to_repo ---------------------------------------------------------------------------------


def test_a_piped_row_becomes_a_workspace_repo_without_an_api_call() -> None:
    # S11: search hits piped while GitHub's API is down still resolve.
    configure(default_org="acme")
    hit = GithubRepo(full_name="acme/api", html_url="https://github.com/acme/api")

    with respx.mock(base_url=API, assert_all_called=False) as mock:
        down = mock.route().mock(return_value=httpx.Response(503))
        repo = piped(hit.model_dump(mode="json"))

    assert down.call_count == 0
    assert (repo.name, repo.url) == ("acme/api", "https://github.com/acme/api.git")
    assert repo.source is not None
    assert (repo.source.plugin, repo.source.kind) == ("github", "github.repo")
    assert GithubRepo.model_validate(repo.source.record) == hit


def test_a_piped_row_keeps_its_branch_description_and_archived_flag() -> None:
    configure(default_org="acme")

    repo = piped(GithubRepo.model_validate(ACME[1]).model_dump(mode="json"))

    assert (repo.default_branch, repo.archived) == ("trunk", True)
    assert repo.url == "https://github.com/acme/web.git"


@pytest.mark.parametrize(
    ("protocol", "row", "url"),
    [
        ("https", ACME[0], "https://github.com/acme/api.git"),
        ("ssh", ACME[0], "git@github.com:acme/api.git"),
        ("https", {"full_name": "acme/api"}, "https://github.com/acme/api.git"),
        ("ssh", {"full_name": "acme/api"}, "git@github.com:acme/api.git"),
    ],
    ids=["https-row", "ssh-row", "https-built", "ssh-built"],
)
def test_git_protocol_picks_the_rows_url_or_builds_one_from_full_name(
    protocol: str, row: dict[str, object], url: str
) -> None:
    configure(default_org="acme", git_protocol=protocol)

    assert piped(GithubRepo.model_validate(row).model_dump(mode="json")).url == url


@pytest.mark.parametrize(
    ("protocol", "url"),
    [
        ("https", "https://ghe.example.com/acme/api.git"),
        ("ssh", "git@ghe.example.com:acme/api.git"),
    ],
)
def test_github_enterprise_urls_are_on_the_web_host_of_the_api(protocol: str, url: str) -> None:
    configure(default_org="acme", base_url="https://ghe.example.com/api/v3", git_protocol=protocol)

    assert piped({"full_name": "acme/api"}).url == url
    assert piped({"full_name": "acme/api", "clone_url": url, "ssh_url": url}).url == url


@pytest.mark.parametrize(
    ("record", "base_url"),
    [
        ({"full_name": "acme/api", "clone_url": "https://evil.example/acme/api.git"}, None),
        ({"full_name": "acme/api", "clone_url": "https://github.com/attacker/x.git"}, None),
        ({"full_name": "acme/api", "clone_url": "file:///tmp/acme/api.git"}, None),
        (
            {"full_name": "acme/api", "clone_url": "https://github.com/acme/api.git"},
            "https://ghe.example.com/api/v3",
        ),
    ],
    ids=["other-host", "other-repo", "file-url", "github-com-row-under-enterprise"],
)
def test_a_row_whose_url_is_not_its_repo_on_githubs_host_is_refused(
    record: dict[str, object], base_url: str | None
) -> None:
    # S17: a forged record never becomes a workspace repo.
    configure(default_org="acme", **({"base_url": base_url} if base_url else {}))

    with pytest.raises(UsageError, match="acme/api") as caught:
        piped(record)

    assert caught.value.exit_code == ExitCode.USAGE


def test_an_ssh_url_on_another_host_is_refused_under_ssh() -> None:
    configure(default_org="acme", git_protocol="ssh")

    with pytest.raises(UsageError):
        piped({"full_name": "acme/api", "ssh_url": "git@evil.example:acme/api.git"})


def test_corpus_and_sweep_rows_are_read_as_github_repos() -> None:
    configure(default_org="acme")
    corpus = {"full_name": "acme/api", "ref": "main", "path": "/store/api"}
    sweep = {"full_name": "acme/web", "refs_matched": ["heads/main"], "hits": {"grep:x": 1}}

    from_corpus = piped(corpus, kind="github.corpus_repo")
    from_sweep = piped(sweep, kind="github.sweep_repo")

    assert from_corpus.url == "https://github.com/acme/api.git"
    assert from_sweep.url == "https://github.com/acme/web.git"
    assert from_corpus.source is not None
    assert from_sweep.source is not None
    assert (from_corpus.source.plugin, from_corpus.source.kind) == ("github", "github.corpus_repo")
    assert (from_sweep.source.plugin, from_sweep.source.kind) == ("github", "github.sweep_repo")
    assert from_corpus.source.record["path"] == "/store/api"


# --- repos -----------------------------------------------------------------------------------


def test_repos_lists_the_inventory_scope_as_workspace_repos() -> None:
    configure(inventory={"orgs": ["acme"]})

    with respx.mock(base_url=API) as mock:
        route = mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        answer = listed()

    assert route.call_count == 1
    assert [(repo.name, repo.url, repo.archived) for repo in answer.value] == [
        ("acme/api", "https://github.com/acme/api.git", False),
        ("acme/web", "https://github.com/acme/web.git", True),
    ]
    assert all(repo.source is not None for repo in answer.value)


def test_repos_follow_git_protocol() -> None:
    configure(default_org="acme", git_protocol="ssh")

    with respx.mock(base_url=API) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME[:1]))
        [repo] = listed().value

    assert repo.url == "git@github.com:acme/api.git"


def test_a_failed_listing_fails_when_no_answer_is_cached() -> None:
    configure(default_org="acme")

    with respx.mock(base_url=API) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(503))
        [answer] = gather(RepoSource.repos, refresh=True)()

    assert isinstance(answer, Failed), answer
    assert (answer.error.category, answer.error.system) == ("unavailable", "github")


def test_after_a_failed_refresh_only_the_answer_cache_is_served_stale() -> None:
    configure(default_org="acme")
    with respx.mock(base_url=API) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        fresh = listed()

    with respx.mock(base_url=API) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(503))
        stale = listed()

    assert fresh.stale is None
    assert stale.value == fresh.value
    assert stale.stale is not None
    assert stale.stale.error.category == "unavailable"


@pytest.mark.parametrize(
    ("response", "category", "hint"),
    [
        (httpx.Response(401, json={"message": "Bad credentials"}), "auth", "auth set github"),
        (
            httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0"},
                json={"message": "API rate limit exceeded for user ID 1."},
            ),
            "unavailable",
            None,
        ),
    ],
    ids=["rejected-token", "rate-limit"],
)
def test_listing_failures_keep_their_category(
    response: httpx.Response, category: str, hint: str | None
) -> None:
    configure(default_org="acme")

    with respx.mock(base_url=API) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=response)
        [answer] = gather(RepoSource.repos, refresh=True)()

    assert isinstance(answer, Failed), answer
    assert (answer.error.category, answer.error.system) == (category, "github")
    if hint is not None:
        assert answer.error.hint is not None
        assert hint in answer.error.hint


def test_a_bare_team_narrows_the_default_org() -> None:
    configure(default_org="acme", inventory={"teams": ["platform"]})

    with respx.mock(base_url=API) as mock:
        mock.get("/orgs/acme/teams/platform/repos").mock(
            return_value=httpx.Response(200, json=ACME[:1])
        )
        assert [repo.name for repo in listed().value] == ["acme/api"]


def test_a_bare_team_resolves_against_the_single_inventory_org() -> None:
    configure(default_org="other", inventory={"orgs": ["acme"], "teams": ["platform"]})

    with respx.mock(base_url=API) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        mock.get("/orgs/acme/teams/platform/repos").mock(
            return_value=httpx.Response(200, json=ACME[:1])
        )
        assert [repo.name for repo in listed().value] == ["acme/api", "acme/web"]


def test_github_enterprise_lists_from_its_own_api() -> None:
    configure(base_url="https://ghe.example.com/api/v3", inventory={"orgs": ["acme"]})
    rows = [{"full_name": "acme/api", "clone_url": "https://ghe.example.com/acme/api.git"}]

    with respx.mock(base_url="https://ghe.example.com/api/v3") as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=rows))
        [repo] = listed().value

    assert repo.url == "https://ghe.example.com/acme/api.git"


# --- inventory scope -------------------------------------------------------------------------


def _settings(**github: object) -> GithubSettings:
    return GithubSettings.model_validate({"token": "ghp_secret", **github})


def test_the_scope_falls_back_to_the_default_org() -> None:
    scope = inventory_scope(_settings(default_org="acme"))

    assert (scope.orgs, scope.teams) == (("acme",), ())


def test_without_a_scope_inventory_scope_is_a_config_error_with_a_hint() -> None:
    with pytest.raises(UntapedError, match=r"github\.inventory\.orgs") as caught:
        inventory_scope(_settings())

    assert caught.value.category == "config"
    assert caught.value.hint is not None
    assert "config set github.inventory.orgs" in caught.value.hint


def test_a_bare_team_without_an_org_is_a_config_error_with_a_hint() -> None:
    with pytest.raises(UntapedError, match="ORG/SLUG") as caught:
        inventory_scope(_settings(inventory={"teams": ["platform"]}))

    assert caught.value.category == "config"
    assert caught.value.hint is not None
    assert "github.default_org" in caught.value.hint
