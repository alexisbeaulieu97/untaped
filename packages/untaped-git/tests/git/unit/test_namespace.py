"""Each plugin's ref namespace, workspace's plain-clone exception, and name checks."""

from __future__ import annotations

import pytest

from untaped_git.domain.namespace import ORIGIN_HEAD, check_names, covered, covers, layout_for


def test_a_plugin_namespace() -> None:
    layout = layout_for("github")
    assert layout.roots == ("refs/untaped/github/heads/", "refs/untaped/github/tags/")
    assert layout.refspecs("heads", ["main", "rel/*"]) == [
        "+refs/heads/main:refs/untaped/github/heads/main",
        "+refs/heads/rel/*:refs/untaped/github/heads/rel/*",
    ]
    assert layout.relative("refs/untaped/github/tags/v1") == "tags/v1"
    assert layout.relative("refs/untaped/ansible/heads/main") is None
    assert layout.absolute("heads/main") == "refs/untaped/github/heads/main"
    assert layout.prunable("tags/v1")


def test_workspace_is_a_plain_clone() -> None:
    layout = layout_for("workspace")
    assert layout.roots == ("refs/remotes/origin/", "refs/tags/")
    assert layout.relative("refs/remotes/origin/main") == "heads/main"
    assert layout.relative(ORIGIN_HEAD) is None
    assert layout.prunable("heads/main")
    assert not layout.prunable("tags/v1")


def test_absolute_refuses_other_shapes() -> None:
    with pytest.raises(ValueError, match="not heads/<branch>"):
        layout_for("github").absolute("remotes/main")


@pytest.mark.parametrize("name", ["main", "feature/x", "rel/*", "v1.2.3", "*"])
def test_good_names(name: str) -> None:
    assert check_names([name]) is None


@pytest.mark.parametrize(
    "name",
    [
        "",
        "-upload-pack=x",
        "refs/heads/main",
        "/main",
        "a:b",
        "+main",
        "a..b",
        "a/*/b/*",
        "main~1",
        "main^",
        "a b",
        "@{u}",
        "x.lock",
        "x/.hidden",
        "trailing/",
        "trailing.",
        "a[0]",
        "a?",
        "a\\b",
    ],
)
def test_bad_names(name: str) -> None:
    assert check_names(["main", name]) is not None


def test_globs_cover_slashes() -> None:
    assert covers("rel/*", "rel/1/hotfix")
    assert covers("*-lts", "v2-lts")
    assert not covers("rel/*", "release")
    assert not covers("a*a", "a")
    assert covers("main", "main")
    assert covered("heads/rel/1", ["rel/*"], [])
    assert not covered("tags/rel/1", ["rel/*"], [])
    assert not covered("other/x", ["*"], ["*"])
