"""Accepted Git URLs and the store key every spelling of one repo shares."""

from __future__ import annotations

import pytest

from untaped_git.domain.url import https_origin, store_key, url_host, validate_git_url


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/app.git",
        "https://git.example:8443/team/sub/app",
        "ssh://git@github.com/acme/app.git",
        "git@github.com:acme/app.git",
    ],
)
def test_accepted_urls(url: str) -> None:
    assert validate_git_url(url) == url


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("https://user:token@github.com/acme/app.git", "must not carry credentials"),
        ("https://token@github.com/acme/app.git", "must not carry credentials"),
        ("file:///srv/app.git", "file:// is not"),
        ("ext::sh -c touch% /tmp/pwned", "is not an https://"),
        ("/srv/app.git", "is not an https://"),
        ("https://github.com/", "no repository path"),
        ("http://github.com/acme/app.git", "http:// is not"),
    ],
)
def test_refused_urls(url: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        validate_git_url(url)


def test_https_and_ssh_spellings_share_one_key() -> None:
    keys = {
        store_key("https://GitHub.com/Acme/App.git"),
        store_key("https://github.com/acme/app"),
        store_key("ssh://git@github.com/acme/app.git"),
        store_key("git@github.com:acme/app.git"),
    }
    assert keys == {("github.com", "acme", "app.git")}


def test_the_key_is_nfc_folded() -> None:
    assert store_key("https://git.example/café/app") == store_key("https://git.example/café/app")


def test_a_hostless_url_hashes_under_unknown() -> None:
    first, second = store_key("/srv/app.git"), store_key("file:///srv/other.git")
    assert first[0] == second[0] == "_unknown"
    assert first != second
    assert first[1].endswith(".git")
    assert len(first) == 2


def test_a_hostile_path_stays_one_segment_each() -> None:
    key = store_key("https://git.example/../../etc/passwd")
    assert all(part not in {"", ".", ".."} and "/" not in part for part in key)


def test_hosts_and_origins() -> None:
    assert url_host("git@GitHub.com:acme/app.git") == "github.com"
    assert url_host("/srv/app.git") is None
    assert https_origin("https://git.example:8443/a/b.git") == "https://git.example:8443"
    assert https_origin("git@github.com:acme/app.git") is None
