"""``RepoFinder``: what the user typed, resolved over every plugin filling ``RepoSource``.

The providers are fakes named ``github`` and ``gitlab`` (the workspace sources
design's scenarios); the finder asks them only through ``untaped.sdk.contracts``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta

import pytest
from pydantic import BaseModel

from git_test_support import composed
from untaped.sdk import ErrorCategory, Source, UntapedError, UsageError, key
from untaped.sdk.contracts import Ambiguous, NotReady, gather
from untaped.sdk.diagnostic import Stale
from untaped.sdk.testing import ManualClock, Running
from untaped_git.api import Repo, RepoFinder, RepoSource
from untaped_git.testing import host, invalid_repo, listing, make_repo, rank

GITLAB = "gitlab.example"


class _Gitlab(BaseModel):
    base_url: str | None = None


class _Github(BaseModel):
    default_org: str | None = None


NO_BASE_URL = NotReady("no base URL", key(_Gitlab).base_url)


@contextmanager
def providers(**plugins: Sequence[type]) -> Iterator[Running]:
    """Git composed with fake plugins, each filling contracts with its provider classes."""
    with composed({name: tuple(p) for name, p in plugins.items()}) as running:
        yield running


def _resolve(ident: str) -> Repo:
    return RepoFinder().resolve(ident)


def test_an_unconfigured_provider_is_not_asked_and_the_other_answers() -> None:
    """S1: gitlab installed but not configured; github's repo is the answer."""
    gitlab = listing(make_repo("acme/api", GITLAB), not_ready=NO_BASE_URL)
    with providers(github=[listing(make_repo("acme/api"))], gitlab=[gitlab]):
        found = _resolve("acme/api")
    assert (found.url, found.source) == (
        "https://github.com/acme/api.git",
        Source(plugin="github", kind="git.repo"),
    )
    assert gitlab.calls == 0


def test_a_repo_only_one_provider_lists_is_that_providers() -> None:
    """S2: a repo only gitlab has, by its full path."""
    with providers(
        github=[listing(make_repo("acme/api"))],
        gitlab=[listing(make_repo("infra/team/api", GITLAB))],
    ):
        found = _resolve("infra/team/api")
    assert (found.name, found.source and found.source.plugin) == ("infra/team/api", "gitlab")


def test_a_repo_two_unranked_providers_list_is_ambiguous_until_ranked() -> None:
    """S3: mirrored in both, nothing ranked: ambiguous (exit 2) with the rank command."""
    lists = {
        "github": [listing(make_repo("acme/api"))],
        "gitlab": [listing(make_repo("acme/api", GITLAB), make_repo("infra/team/api", GITLAB))],
    }
    with providers(**lists), pytest.raises(Ambiguous) as caught:
        _resolve("acme/api")
    assert isinstance(caught.value, UsageError)
    assert caught.value.hint == "run `untaped plugin rank git.repo_source repos github gitlab`"


def test_ranking_picks_the_first_ranked_providers_repo() -> None:
    """S3, ranked: github's acme/api; the repo only gitlab lists stays gitlab's."""
    rank("github", "gitlab")
    lists = {
        "github": [listing(make_repo("acme/api"))],
        "gitlab": [listing(make_repo("acme/api", GITLAB), make_repo("infra/team/api", GITLAB))],
    }
    with providers(**lists):
        finder = RepoFinder()
        mirrored, only_gitlab = finder.resolve("acme/api"), finder.resolve("infra/team/api")
    assert mirrored.url == "https://github.com/acme/api.git"
    assert only_gitlab.source is not None and only_gitlab.source.plugin == "gitlab"


HTTP_503 = UntapedError("HTTP 503", category="unavailable", system="github", hint="retry later")


def test_a_stale_listing_without_the_repo_is_the_providers_failure(clock: ManualClock) -> None:
    """S4: rank [github, gitlab], github down and its stored answer lacks acme/api: github's
    error (exit 5) and hint, never gitlab's match below it."""
    rank("github", "gitlab")
    github = listing(make_repo("acme/other"))
    with providers(github=[github], gitlab=[listing(make_repo("acme/api", GITLAB))]) as running:
        gather(RepoSource.repos)()
        clock.advance(timedelta(hours=7))
        github.error = HTTP_503
        with pytest.raises(UntapedError) as caught:
            _resolve("acme/api")
    emitted = running.diagnostics
    assert caught.value is HTTP_503
    assert caught.value.category == ErrorCategory.UNAVAILABLE
    assert caught.value.hint == "retry later"
    assert [each for each in emitted if isinstance(each, Stale)] == []  # the error says it


def test_a_match_ranked_above_the_failed_provider_wins_with_a_warning(
    capsys: pytest.CaptureFixture[str],
    clock: ManualClock,
) -> None:
    """S4: rank [gitlab, github], github down: gitlab's match stands, with a warning."""
    rank("gitlab", "github")
    github = listing(make_repo("acme/other"))
    with providers(github=[github], gitlab=[listing(make_repo("acme/api", GITLAB))]):
        gather(RepoSource.repos)()
        clock.advance(timedelta(hours=7))
        github.error = HTTP_503
        found = _resolve("acme/api")
    assert found.source is not None and found.source.plugin == "gitlab"
    assert (
        "warning: github failed (HTTP 503); using a match ranked above it"
        in capsys.readouterr().err
    )


def test_a_stale_listing_that_has_the_repo_answers_with_one_stale_line(clock: ManualClock) -> None:
    """S4: github down, but its stored answer has acme/api: github's, said once, no error."""
    rank("github", "gitlab")
    github = listing(make_repo("acme/api"))
    with providers(github=[github], gitlab=[listing(make_repo("acme/api", GITLAB))]) as running:
        gather(RepoSource.repos)()
        clock.advance(timedelta(hours=7))
        github.error = HTTP_503
        found = _resolve("acme/api")
    emitted = running.diagnostics
    assert found.url == "https://github.com/acme/api.git"
    assert github.calls == 2  # the second call failed; the stored answer stood in
    [stale] = emitted
    assert isinstance(stale, Stale)
    assert (stale.plugin, stale.cache) == ("github", "git.repo_source_repos")
    assert stale.hint is None  # the finder has no --cached to suggest


def test_an_expired_token_blocks_with_its_own_category_never_not_found() -> None:
    """S6: gitlab's token expired, nothing ranked: gitlab's auth error (exit 4)."""
    expired = UntapedError(
        "401 Unauthorized", category="auth", system="gitlab", hint="run `untaped auth login gitlab`"
    )
    with (
        providers(github=[listing(make_repo("acme/api"))], gitlab=[listing(error=expired)]),
        pytest.raises(UntapedError) as caught,
    ):
        _resolve("acme/api")
    assert caught.value is expired
    assert caught.value.category == ErrorCategory.AUTH


def test_an_invalid_row_of_an_unranked_provider_warns_and_the_other_answers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S8: gitlab returns an invalid row and is unranked: a warning, github's ``api``."""
    gitlab = listing(make_repo("infra/web", GITLAB), invalid_repo("infra//api"))
    with providers(github=[listing(make_repo("acme/api"))], gitlab=[gitlab]):
        found = _resolve("api")
    assert found.url == "https://github.com/acme/api.git"
    err = capsys.readouterr().err
    assert "warning: gitlab wasn't asked (invalid-item): 1 invalid: row 2: " in err


def test_an_invalid_row_of_a_provider_ranked_first_is_a_config_error() -> None:
    """S8: gitlab ranked first returns an invalid row: exit 4, "upgrade untaped-gitlab"."""
    rank("gitlab", "github")
    gitlab = listing(make_repo("infra/web", GITLAB), invalid_repo("infra//api"))
    with (
        providers(github=[listing(make_repo("acme/api"))], gitlab=[gitlab]),
        pytest.raises(UntapedError) as caught,
    ):
        _resolve("api")
    assert caught.value.category == ErrorCategory.CONFIG
    assert caught.value.hint == "upgrade untaped-gitlab"


def test_a_typed_url_is_a_plain_repo_named_by_its_full_path_and_asks_no_provider() -> None:
    """S9: a URL becomes ``infra/team/api`` with no source; no provider is asked."""
    github, gitlab = listing(make_repo("acme/api")), listing(make_repo("infra/team/api", GITLAB))
    with providers(github=[github], gitlab=[gitlab]):
        found = _resolve("https://gitlab.example.com/infra/team/api.git")
    assert found == Repo(name="infra/team/api", url="https://gitlab.example.com/infra/team/api.git")
    assert found.source is None
    assert (github.calls, gitlab.calls) == (0, 0)


@pytest.mark.parametrize(
    "url", ["file:///srv/git/api.git", "/srv/git/api.git", "https://user:pw@h.example/a/b.git"]
)
def test_a_typed_url_untaped_refuses_is_a_usage_error(url: str) -> None:
    with providers(github=[listing(make_repo("acme/api"))]), pytest.raises(UsageError) as caught:
        _resolve(url)
    assert caught.value.hint == "pass an https:// or ssh:// clone URL, or a repo name"


def test_with_no_provider_ready_a_name_is_not_found_naming_the_setting() -> None:
    """S12: github has no inventory scope: not found (exit 2) naming why and the setting;
    no URL is guessed from the name."""
    github = listing(
        make_repo("acme/api"), not_ready=NotReady("no inventory scope", key(_Github).default_org)
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
        "no provider of git.repo_source.repos is ready: no installed plugin fills it; "
        "install or upgrade a plugin that lists repos, "
        "or pass the repo's clone URL"
    )


def test_a_listed_url_on_another_host_than_its_source_serves_is_refused() -> None:
    """S17 (the finder's side): github serves github.com, so its repo on evil.example is
    refused (exit 2) before anything is fetched."""
    forged = Repo(name="acme/api", url="https://evil.example/acme/api.git")
    with (
        providers(github=[listing(forged), host("github.com")]),
        pytest.raises(UsageError) as caught,
    ):
        _resolve("acme/api")
    assert "github serves github.com: refusing to fetch it" in str(caught.value)


def test_admit_checks_a_chosen_repo_against_its_source_host() -> None:
    """S17: a piped or picked repo passes the same check; a plugin serving no host is not
    checked, and neither is a repo without a source."""
    on_github = Source(plugin="github", kind="git.repo")
    forged = make_repo("acme/api", "evil.example", source=on_github)
    with providers(github=[listing(), host("GitHub.com")], gitlab=[listing()]):
        finder = RepoFinder()
        with pytest.raises(UsageError):
            finder.admit(forged)
        genuine = make_repo("acme/api", source=on_github)
        assert finder.admit(genuine) is genuine
        unchecked = make_repo("acme/api", "evil.example", source=Source(plugin="gitlab", kind=""))
        assert finder.admit(unchecked) is unchecked
        typed = make_repo("acme/api", "evil.example")
        assert finder.admit(typed) is typed


def test_owner_slash_name_matches_exactly_in_any_case() -> None:
    with providers(
        github=[listing(make_repo("acme/api", default_branch="develop"), make_repo("x/acme"))]
    ):
        found = _resolve("ACME/api")
    assert (found.name, found.default_branch) == ("acme/api", "develop")


def test_a_bare_name_matches_the_last_segment() -> None:
    with providers(github=[listing(make_repo("acme/api"), make_repo("acme/web"))]):
        assert _resolve("API").name == "acme/api"


def test_a_bare_name_two_repos_of_one_provider_share_is_ambiguous() -> None:
    with (
        providers(github=[listing(make_repo("acme/web"), make_repo("other/web"))]),
        pytest.raises(Ambiguous, match="2 items from github match"),
    ):
        _resolve("web")


def test_an_unknown_name_says_who_wasnt_asked() -> None:
    gitlab = listing(not_ready=NO_BASE_URL)
    with (
        providers(github=[listing(make_repo("acme/api"))], gitlab=[gitlab]),
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
        providers(github=[listing(make_repo("acme/api"), make_repo("acme/web"))]),
        pytest.raises(UsageError, match="did you mean") as caught,
    ):
        _resolve(ident)
    assert suggested in str(caught.value)


def test_the_providers_are_asked_once_per_finder() -> None:
    """A failed answer is not stored, so a second ask would call again."""
    down = listing(error=HTTP_503)
    with providers(github=[down]):
        finder = RepoFinder()
        for ident in ("api", "web"):
            with pytest.raises(UntapedError, match="HTTP 503"):
                finder.resolve(ident)
    assert down.calls == 1


def test_reask_asks_the_repos_source_live_and_admits_what_it_lists_now() -> None:
    github = listing(make_repo("acme/api"))
    gitlab = listing(make_repo("infra/api", GITLAB))
    with providers(github=[github, host("github.com")], gitlab=[gitlab]):
        finder = RepoFinder()
        saved = finder.resolve("acme/api")  # github's answer is stored, fresh
        github.rows = [make_repo("acme/api", description="moved")]
        again = finder.reask(saved)
        assert again is not None and again.description == "moved"
        assert (github.calls, gitlab.calls) == (2, 1)  # live, and only the source is asked
        assert finder.reask(finder.resolve("https://github.com/acme/api.git")) is None
        github.rows = [make_repo("acme/api", "evil.example")]
        with pytest.raises(UsageError, match="refusing to fetch it"):
            RepoFinder().reask(saved)


def test_reask_lists_each_source_once_however_many_repos_it_sourced() -> None:
    github = listing(make_repo("acme/api"), make_repo("acme/web"), make_repo("acme/docs"))
    with providers(github=[github, host("github.com")]):
        saved = [RepoFinder().resolve(name) for name in ("acme/api", "acme/web", "acme/docs")]
        calls = github.calls
        finder = RepoFinder()
        assert [finder.reask(each) for each in saved] == saved
        assert github.calls == calls + 1


def test_reask_of_a_source_that_is_down_raises_its_error_not_the_stored_answer() -> None:
    github = listing(make_repo("acme/api"))
    with providers(github=[github]) as running:
        finder = RepoFinder()
        saved = finder.resolve("acme/api")  # github's answer is stored
        github.error = HTTP_503
        with pytest.raises(UntapedError) as caught:
            finder.reask(saved)
    emitted = running.diagnostics
    assert caught.value is HTTP_503
    assert [each for each in emitted if isinstance(each, Stale)] == []  # the error says it


def test_reask_of_a_source_that_is_gone_or_not_ready_is_none() -> None:
    gitlab = listing(make_repo("infra/api", GITLAB), not_ready=NO_BASE_URL)
    with providers(github=[listing(make_repo("acme/api"))], gitlab=[gitlab]):
        finder = RepoFinder()
        from_gitlab = make_repo("infra/api", GITLAB, source=Source(plugin="gitlab", kind=""))
        assert finder.reask(from_gitlab) is None
        gone = make_repo("infra/api", GITLAB, source=Source(plugin="gitea", kind=""))
        assert finder.reask(gone) is None
