"""Git's ``RepoSource`` contract: the checks it runs on any provider, its answer cache, and
what a typed ident is.

Its schemas are guarded by the plugin's surface (``contract git.*`` lines).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta

import pytest

from git_test_support import composed
from untaped.sdk import Record, home
from untaped.sdk.contracts import Provider, gather
from untaped.sdk.testing import ManualClock, piped_through
from untaped_git.api import Repo, RepoSelector, RepoSource, looks_like_url
from untaped_git.testing import conformance, host, listing, make_repo


def test_a_provider_listing_distinct_names_conforms() -> None:
    github = listing(make_repo("acme/api"), make_repo("acme/web"), make_repo("other/api"))
    conformance(github())


def test_a_provider_listing_one_name_twice_does_not_conform() -> None:
    """Repos are found by name: two repos under one name (in any case) can't be told
    apart."""
    github = listing(
        make_repo("acme/api"), make_repo("ACME/API", "mirror.example"), make_repo("acme/web")
    )
    with pytest.raises(AssertionError, match="repos share a name: acme/api"):
        conformance(github())


class GroupSelector(Record):
    """A forge's own selector: the groups whose repos to take."""

    groups: list[str]


class Groups(RepoSource, Provider):
    """Lists the owner's repos and issues selectors from its own."""

    def to_selector(self, item: GroupSelector) -> RepoSelector:
        return RepoSelector()

    def repos(self) -> list[Repo]:
        return [make_repo("infra/platform/base")]


def test_a_provider_of_repos_issues_a_selector_from_its_own_record() -> None:
    """A provider whose ``T`` is ``Repo`` fills the record bridge ``to_selector``; the piped
    selector comes back as a ``git.repo_selector`` carrying the provider's own record."""
    with composed({"gitlab": (Groups,)}):
        selector = piped_through(RepoSource.to_selector, GroupSelector(groups=["infra"]))
    assert isinstance(selector, RepoSelector)
    assert selector.source is not None
    assert selector.source.model_dump() == {
        "plugin": "gitlab",
        "kind": "gitlab.group_selector",
        "record": {"groups": ["infra"]},
    }


def test_a_git_host_provider_has_nothing_to_conform_to() -> None:
    conformance(host("github.com")())


_BLOCK_PYTEST = """
import sys

class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name == "pytest" or name.startswith(("_pytest", "pytest.")):
            raise ImportError(name)

sys.meta_path.insert(0, Blocked())
from untaped_git.testing import conformance
assert callable(conformance)
"""


def test_the_conformance_check_loads_without_pytest() -> None:
    """untaped's plugin check imports ``untaped_git.testing`` where pytest may be missing,
    and skips an owner whose module fails to import, so the import must not need pytest."""
    ran = subprocess.run([sys.executable, "-c", _BLOCK_PYTEST], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr


def test_repos_answers_are_kept_six_hours(clock: ManualClock) -> None:
    github = listing(make_repo("acme/api"))
    with composed({"github": (github,)}):
        gather(RepoSource.repos)()
        clock.advance(timedelta(hours=5))
        gather(RepoSource.repos)()
        assert github.calls == 1
        clock.advance(timedelta(hours=2))
        gather(RepoSource.repos)()
    assert github.calls == 2


@pytest.mark.parametrize("by", ["config", "env"])
def test_a_max_age_of_zero_asks_the_providers_every_time(
    by: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if by == "env":
        monkeypatch.setenv("UNTAPED_GIT__CACHES__REPO_SOURCE_REPOS__MAX_AGE_SECONDS", "0")
    else:
        path = home().expanduser() / "config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "format_version: 2\ngit:\n  caches:\n    repo_source_repos: {max_age_seconds: 0}\n",
            encoding="utf-8",
        )
    github = listing(make_repo("acme/api"))
    with composed({"github": (github,)}):
        gather(RepoSource.repos)()
        gather(RepoSource.repos)()
    assert github.calls == 2


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
