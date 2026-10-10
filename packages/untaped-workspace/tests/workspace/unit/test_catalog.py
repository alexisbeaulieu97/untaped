"""``RepoSources``: what the user typed, resolved over every plugin filling ``RepoSource``.

The providers are fakes named ``github`` and ``gitlab`` (design §15's scenarios);
workspace asks them only through ``untaped.contracts``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta

import pytest

from untaped.contracts import Ambiguous, Contract, NotReady, Source, gather
from untaped.sdk import ErrorCategory, UntapedError, UsageError
from untaped.testing import compose_with
from untaped_git import SPEC as GIT
from untaped_workspace import SPEC as WORKSPACE
from untaped_workspace.api import Repo, RepoSource
from untaped_workspace.infrastructure.catalog import RepoSources
from workspace.fakes import Host, Listing, age_answers, invalid_repo, rank, repo

GITLAB = "gitlab.example"
NO_BASE_URL = NotReady("no base URL", setting="gitlab.base_url")


@contextmanager
def providers(**plugins: Sequence[Contract]) -> Iterator[None]:
    """Git and workspace composed with fake plugins, each offering its providers."""
    with compose_with(GIT, WORKSPACE, provides={name: list(p) for name, p in plugins.items()}):
        yield


def _resolve(ident: str) -> Repo:
    return RepoSources().resolve(ident)


def test_an_unconfigured_provider_is_not_asked_and_the_other_answers() -> None:
    """S1: gitlab installed but not configured; github's repo is the answer."""
    gitlab = Listing(repo("acme/api", GITLAB), not_ready=NO_BASE_URL)
    with providers(github=[Listing(repo("acme/api"))], gitlab=[gitlab]):
        found = _resolve("acme/api")
    assert (found.url, found.source) == (
        "https://github.com/acme/api.git",
        Source(plugin="github", kind="workspace.repo"),
    )
    assert gitlab.calls == 0


def test_a_repo_only_one_provider_lists_is_that_providers() -> None:
    """S2: a repo only gitlab has, by its full path."""
    with providers(
        github=[Listing(repo("acme/api"))], gitlab=[Listing(repo("infra/team/api", GITLAB))]
    ):
        found = _resolve("infra/team/api")
    assert (found.name, found.source and found.source.plugin) == ("infra/team/api", "gitlab")


def test_a_repo_two_unranked_providers_list_is_ambiguous_until_ranked() -> None:
    """S3: mirrored in both, nothing ranked: ambiguous (exit 2) with the rank command."""
    lists = {
        "github": [Listing(repo("acme/api"))],
        "gitlab": [Listing(repo("acme/api", GITLAB), repo("infra/team/api", GITLAB))],
    }
    with providers(**lists), pytest.raises(Ambiguous) as caught:
        _resolve("acme/api")
    assert isinstance(caught.value, UsageError)
    assert (
        caught.value.hint == "run `untaped plugin rank workspace.repo_source repos github gitlab`"
    )


def test_ranking_picks_the_first_ranked_providers_repo() -> None:
    """S3, ranked: github's acme/api; the repo only gitlab lists stays gitlab's."""
    rank("github", "gitlab")
    lists = {
        "github": [Listing(repo("acme/api"))],
        "gitlab": [Listing(repo("acme/api", GITLAB), repo("infra/team/api", GITLAB))],
    }
    with providers(**lists):
        catalog = RepoSources()
        mirrored, only_gitlab = catalog.resolve("acme/api"), catalog.resolve("infra/team/api")
    assert mirrored.url == "https://github.com/acme/api.git"
    assert only_gitlab.source is not None and only_gitlab.source.plugin == "gitlab"


HTTP_503 = UntapedError("HTTP 503", category="unavailable", system="github", hint="retry later")


def test_a_stale_listing_without_the_repo_is_the_providers_failure() -> None:
    """S4: rank [github, gitlab], github down and its stored answer lacks acme/api: github's
    error (exit 5) and hint, never gitlab's match below it."""
    rank("github", "gitlab")
    github = Listing(repo("acme/other"))
    with providers(github=[github], gitlab=[Listing(repo("acme/api", GITLAB))]):
        gather(RepoSource.repos)()
        age_answers(timedelta(hours=7))
        github.error = HTTP_503
        with pytest.raises(UntapedError) as caught:
            _resolve("acme/api")
    assert caught.value is HTTP_503
    assert caught.value.category == ErrorCategory.UNAVAILABLE
    assert caught.value.hint == "retry later"


def test_a_match_ranked_above_the_failed_provider_wins_with_a_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S4: rank [gitlab, github], github down: gitlab's match stands, with a warning."""
    rank("gitlab", "github")
    github = Listing(repo("acme/other"))
    with providers(github=[github], gitlab=[Listing(repo("acme/api", GITLAB))]):
        gather(RepoSource.repos)()
        age_answers(timedelta(hours=7))
        github.error = HTTP_503
        found = _resolve("acme/api")
    assert found.source is not None and found.source.plugin == "gitlab"
    assert (
        "warning: github failed (HTTP 503); using a match ranked above it"
        in capsys.readouterr().err
    )


def test_a_stale_listing_that_has_the_repo_answers_without_an_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S4: github down, but its stored answer has acme/api: github's, no error shown."""
    rank("github", "gitlab")
    github = Listing(repo("acme/api"))
    with providers(github=[github], gitlab=[Listing(repo("acme/api", GITLAB))]):
        gather(RepoSource.repos)()
        age_answers(timedelta(hours=7))
        github.error = HTTP_503
        found = _resolve("acme/api")
    assert found.url == "https://github.com/acme/api.git"
    assert github.calls == 2  # the second call failed; the stored answer stood in
    assert capsys.readouterr().err == ""


def test_an_expired_token_blocks_with_its_own_category_never_not_found() -> None:
    """S6: gitlab's token expired, nothing ranked: gitlab's auth error (exit 4)."""
    expired = UntapedError(
        "401 Unauthorized", category="auth", system="gitlab", hint="run `untaped auth set gitlab`"
    )
    with (
        providers(github=[Listing(repo("acme/api"))], gitlab=[Listing(error=expired)]),
        pytest.raises(UntapedError) as caught,
    ):
        _resolve("acme/api")
    assert caught.value is expired
    assert caught.value.category == ErrorCategory.AUTH


def test_an_invalid_row_of_an_unranked_provider_warns_and_the_other_answers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S8: gitlab returns an invalid row and is unranked: a warning, github's ``api``."""
    gitlab = Listing(repo("infra/web", GITLAB), invalid_repo("infra//api"))
    with providers(github=[Listing(repo("acme/api"))], gitlab=[gitlab]):
        found = _resolve("api")
    assert found.url == "https://github.com/acme/api.git"
    err = capsys.readouterr().err
    assert "warning: gitlab wasn't asked (invalid-item): 1 invalid: row 2: " in err


def test_an_invalid_row_of_a_provider_ranked_first_is_a_config_error() -> None:
    """S8: gitlab ranked first returns an invalid row: exit 4, "upgrade untaped-gitlab"."""
    rank("gitlab", "github")
    gitlab = Listing(repo("infra/web", GITLAB), invalid_repo("infra//api"))
    with (
        providers(github=[Listing(repo("acme/api"))], gitlab=[gitlab]),
        pytest.raises(UntapedError) as caught,
    ):
        _resolve("api")
    assert caught.value.category == ErrorCategory.CONFIG
    assert caught.value.hint == "upgrade untaped-gitlab"


def test_a_typed_url_is_a_plain_repo_named_by_its_full_path_and_asks_no_provider() -> None:
    """S9: a URL becomes ``infra/team/api`` with no source; no provider is asked."""
    github, gitlab = Listing(repo("acme/api")), Listing(repo("infra/team/api", GITLAB))
    with providers(github=[github], gitlab=[gitlab]):
        found = _resolve("https://gitlab.example.com/infra/team/api.git")
    assert found == Repo(name="infra/team/api", url="https://gitlab.example.com/infra/team/api.git")
    assert found.source is None
    assert (github.calls, gitlab.calls) == (0, 0)


@pytest.mark.parametrize(
    "url", ["file:///srv/git/api.git", "/srv/git/api.git", "https://user:pw@h.example/a/b.git"]
)
def test_a_typed_url_untaped_refuses_is_a_usage_error(url: str) -> None:
    with providers(github=[Listing(repo("acme/api"))]), pytest.raises(UsageError) as caught:
        _resolve(url)
    assert caught.value.hint == "pass an https:// or ssh:// clone URL, or a repo name"


def test_with_no_provider_ready_a_name_is_not_found_naming_the_setting() -> None:
    """S12: github has no inventory scope: not found (exit 2) naming why and the setting;
    no URL is guessed from the name."""
    github = Listing(
        repo("acme/api"), not_ready=NotReady("no inventory scope", setting="github.default_org")
    )
    with providers(github=[github]), pytest.raises(UsageError) as caught:
        _resolve("acme/api")
    assert str(caught.value) == "repo not found: 'acme/api'"
    assert caught.value.hint == (
        "github wasn't asked: no inventory scope; set github.default_org, "
        "or pass the repo's clone URL"
    )
    assert github.calls == 0


def test_with_no_provider_installed_a_name_is_not_found() -> None:
    with providers(), pytest.raises(UsageError) as caught:
        _resolve("acme/api")
    assert caught.value.hint == (
        "no provider of workspace.repo_source.repos is ready: no installed plugin fills it; "
        "install or upgrade a plugin that lists repos (untaped[github]), "
        "or pass the repo's clone URL"
    )


def test_a_listed_url_on_another_host_than_its_source_serves_is_refused() -> None:
    """S17 (workspace's side): github serves github.com, so its repo on evil.example is
    refused (exit 2) before anything is fetched."""
    forged = Repo(name="acme/api", url="https://evil.example/acme/api.git")
    with (
        providers(github=[Listing(forged), Host("github.com")]),
        pytest.raises(UsageError) as caught,
    ):
        _resolve("acme/api")
    assert "github serves github.com: refusing to fetch it" in str(caught.value)


def test_admit_checks_a_chosen_repo_against_its_source_host() -> None:
    """S17: a piped or picked repo passes the same check; a plugin serving no host is not
    checked, and neither is a repo without a source."""
    on_github = Source(plugin="github", kind="workspace.repo")
    forged = repo("acme/api", "evil.example", source=on_github)
    with providers(github=[Listing(), Host("GitHub.com")], gitlab=[Listing()]):
        catalog = RepoSources()
        with pytest.raises(UsageError):
            catalog.admit(forged)
        genuine = repo("acme/api", source=on_github)
        assert catalog.admit(genuine) is genuine
        unchecked = repo("acme/api", "evil.example", source=Source(plugin="gitlab", kind=""))
        assert catalog.admit(unchecked) is unchecked
        typed = repo("acme/api", "evil.example")
        assert catalog.admit(typed) is typed


def test_owner_slash_name_matches_exactly_in_any_case() -> None:
    with providers(github=[Listing(repo("acme/api", default_branch="develop"), repo("x/acme"))]):
        found = _resolve("ACME/api")
    assert (found.name, found.default_branch) == ("acme/api", "develop")


def test_a_bare_name_matches_the_last_segment() -> None:
    with providers(github=[Listing(repo("acme/api"), repo("acme/web"))]):
        assert _resolve("API").name == "acme/api"


def test_a_bare_name_two_repos_of_one_provider_share_is_ambiguous() -> None:
    with (
        providers(github=[Listing(repo("acme/web"), repo("other/web"))]),
        pytest.raises(Ambiguous, match="2 items from github match"),
    ):
        _resolve("web")


def test_an_unknown_name_says_who_wasnt_asked() -> None:
    gitlab = Listing(not_ready=NO_BASE_URL)
    with (
        providers(github=[Listing(repo("acme/api"))], gitlab=[gitlab]),
        pytest.raises(UsageError) as caught,
    ):
        _resolve("nope")
    assert str(caught.value) == "repo not found: 'nope'; not asked: gitlab (not-configured)"
    assert caught.value.hint == "pass the repo's clone URL"


@pytest.mark.parametrize(
    ("ident", "suggested"), [("acme/apii", "'acme/api'"), ("wbe", "'acme/web'")]
)
def test_an_unknown_name_suggests_close_matches(ident: str, suggested: str) -> None:
    with (
        providers(github=[Listing(repo("acme/api"), repo("acme/web"))]),
        pytest.raises(UsageError, match="did you mean") as caught,
    ):
        _resolve(ident)
    assert suggested in str(caught.value)


def test_the_providers_are_asked_once_per_catalog() -> None:
    """A failed answer is not stored, so a second ask would call again."""
    down = Listing(error=HTTP_503)
    with providers(github=[down]):
        catalog = RepoSources()
        for ident in ("api", "web"):
            with pytest.raises(UntapedError, match="HTTP 503"):
                catalog.resolve(ident)
    assert down.calls == 1


def test_reask_asks_the_repos_source_live_and_admits_what_it_lists_now() -> None:
    github = Listing(repo("acme/api"))
    gitlab = Listing(repo("infra/api", GITLAB))
    with providers(github=[github, Host("github.com")], gitlab=[gitlab]):
        catalog = RepoSources()
        saved = catalog.resolve("acme/api")  # github's answer is stored, fresh
        github.rows = [repo("acme/api", description="moved")]
        again = catalog.reask(saved)
        assert again is not None and again.description == "moved"
        assert (github.calls, gitlab.calls) == (2, 1)  # live, and only the source is asked
        assert catalog.reask(catalog.resolve("https://github.com/acme/api.git")) is None
        github.rows = [repo("acme/api", "evil.example")]
        with pytest.raises(UsageError, match="refusing to fetch it"):
            RepoSources().reask(saved)


def test_reask_lists_each_source_once_however_many_repos_it_sourced() -> None:
    github = Listing(repo("acme/api"), repo("acme/web"), repo("acme/docs"))
    with providers(github=[github, Host("github.com")]):
        saved = [RepoSources().resolve(name) for name in ("acme/api", "acme/web", "acme/docs")]
        calls = github.calls
        catalog = RepoSources()
        assert [catalog.reask(each) for each in saved] == saved
        assert github.calls == calls + 1


def test_reask_of_a_source_that_is_down_raises_its_error_not_the_stored_answer() -> None:
    github = Listing(repo("acme/api"))
    with providers(github=[github]):
        catalog = RepoSources()
        saved = catalog.resolve("acme/api")  # github's answer is stored
        github.error = HTTP_503
        with pytest.raises(UntapedError) as caught:
            catalog.reask(saved)
    assert caught.value is HTTP_503


def test_reask_of_a_source_that_is_gone_or_not_ready_is_none() -> None:
    gitlab = Listing(repo("infra/api", GITLAB), not_ready=NO_BASE_URL)
    with providers(github=[Listing(repo("acme/api"))], gitlab=[gitlab]):
        catalog = RepoSources()
        from_gitlab = repo("infra/api", GITLAB, source=Source(plugin="gitlab", kind=""))
        assert catalog.reask(from_gitlab) is None
        gone = repo("infra/api", GITLAB, source=Source(plugin="gitea", kind=""))
        assert catalog.reask(gone) is None
