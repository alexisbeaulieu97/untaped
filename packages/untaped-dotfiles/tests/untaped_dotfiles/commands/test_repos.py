"""Working copies: an item's ``repos:`` entries are cloned once and then left to the person."""

from __future__ import annotations

from pathlib import Path

import pytest

from dotfiles_test_support import (
    MakeRemote,
    as_json,
    git,
    installed,
    json_rows,
    publish,
    run,
    states,
    subscribe,
)

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def _package(url: str, target: str = "~/Projects/app", extra: str = "") -> str:
    return f"""\
version: 1
description: the repos I work in
{extra}items:
  work:
    repos:
      - {{url: "{url}", target: "{target}"}}
"""


def _subscribed(make_remote: MakeRemote, url: str, **kw: str) -> None:
    source = make_remote(packages={"code": _package(url, **kw)}, files={})
    subscribe(source.url, "--add", "code")


def _repo_rows(args: list[str]) -> tuple[int, list[dict[str, object]]]:
    result = run([*args, "--format", "json"])
    return result.exit_code, [r for r in json_rows(result) if r["kind"] == "repo"]


def test_an_absent_target_is_cloned_once(make_remote: MakeRemote, user_home: Path) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    _subscribed(make_remote, app.url)
    assert states()["~/Projects/app"] == "pending"
    code, rows = _repo_rows(["apply", "--dry-run"])
    assert code == 0 and rows[0]["detail"] == f"clones {app.url}"
    assert not (user_home / "Projects/app").exists()  # a dry run clones nothing
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 0 and rows[0]["action"] == "created"
    assert (user_home / "Projects/app/README").read_text() == "app\n"
    assert "helper" not in (user_home / "Projects/app/.git/config").read_text()
    assert states()["~/Projects/app"] == "applied"
    (user_home / "Projects/app/mine.txt").write_text("my work\n")
    app.commit("README", "moved on\n")
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 0 and rows[0]["action"] == "unchanged"  # never moved: it is the person's
    assert (user_home / "Projects/app/README").read_text() == "app\n"


def test_an_existing_checkout_of_the_url_is_applied(
    make_remote: MakeRemote, user_home: Path
) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    folder = user_home / "Projects/app"
    folder.mkdir(parents=True)
    git(folder, "init", "-q")
    git(folder, "remote", "add", "origin", app.url)
    _subscribed(make_remote, app.url)
    assert states()["~/Projects/app"] == "applied"
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 0 and rows[0]["action"] == "unchanged"
    assert not (folder / "README").exists()


def test_another_folder_is_foreign_and_untouched(make_remote: MakeRemote, user_home: Path) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    folder = user_home / "Projects/app"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("mine\n")
    _subscribed(make_remote, app.url)
    rows = {r["entry"]: r for r in as_json(["status"])}
    assert rows["~/Projects/app"]["state"] == "foreign"
    assert "isn't a git checkout" in str(rows["~/Projects/app"]["detail"])
    assert rows["~/Projects/app"]["policy"] is None
    code, repo_rows = _repo_rows(["apply", "--yes"])
    assert code == 3 and repo_rows[0]["action"] == "skipped"
    assert [p.name for p in folder.iterdir()] == ["notes.txt"]
    git(folder, "init", "-q")
    git(folder, "remote", "add", "origin", "https://git.example/other.git")
    assert "a checkout of https://git.example/other.git" in str(
        {r["entry"]: r for r in as_json(["status"])}["~/Projects/app"]["detail"]
    )


def test_remove_forgets_the_copy_and_leaves_it(make_remote: MakeRemote, user_home: Path) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    _subscribed(make_remote, app.url)
    assert _repo_rows(["apply", "--yes"])[0] == 0
    code, rows = _repo_rows(["remove", "code", "--yes"])
    assert code == 0
    assert rows[0]["detail"] == "forgotten; the working copy stays, it is yours"
    assert (user_home / "Projects/app/README").exists()
    assert "~/Projects/app" not in states()


def test_unsubscribe_forgets_the_copy_and_leaves_it(
    make_remote: MakeRemote, user_home: Path
) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    _subscribed(make_remote, app.url)
    assert _repo_rows(["apply", "--yes"])[0] == 0
    code, rows = _repo_rows(["unsubscribe", "dotfiles", "--yes"])
    assert code == 0 and rows[0]["action"] == "unchanged"
    assert (user_home / "Projects/app/README").exists()


def test_a_copy_of_an_item_turned_off_is_off(make_remote: MakeRemote, user_home: Path) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    _subscribed(make_remote, app.url)
    assert _repo_rows(["apply", "--yes"])[0] == 0
    ran = installed().cli(
        ["config", "set", "dotfiles.sources.dotfiles.packages.code.items.work.enabled", "false"]
    )
    assert int(ran.exit) == 0, ran.stderr
    rows = {r["entry"]: r for r in as_json(["status"])}
    assert rows["~/Projects/app"]["state"] == "off"
    assert "remove dotfiles/code/work" in str(rows["~/Projects/app"]["detail"])


def test_a_target_input_waits_for_a_value(make_remote: MakeRemote, user_home: Path) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    _subscribed(
        make_remote,
        app.url,
        target="~/{{ dir }}/app",
        extra="inputs:\n  dir: {description: Where projects go}\n",
    )
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 3 and rows[0]["action"] == "skipped"
    assert "--var dir=VALUE" in str(rows[0]["detail"])
    code, rows = _repo_rows(["apply", "--yes", "--var", "dir=src"])
    assert code == 0 and rows[0]["action"] == "created"
    assert (user_home / "src/app/README").exists()


def test_a_clone_that_fails_fails_its_row(make_remote: MakeRemote, user_home: Path) -> None:
    _subscribed(make_remote, "https://git.example/missing.git")
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 1 and rows[0]["action"] == "failed"
    assert states()["~/Projects/app"] == "pending"


def test_a_copy_the_package_drops_is_forgotten(make_remote: MakeRemote, user_home: Path) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    source = make_remote(packages={"code": _package(app.url)}, files={"notes.txt": "n\n"})
    subscribe(source.url, "--add", "code")
    assert _repo_rows(["apply", "--yes"])[0] == 0
    dropped = _package(app.url).split("    repos:")[0] + (
        "    files:\n      - {path: notes.txt, target: ~/notes.txt, mode: copy}\n"
    )
    publish(source, {".untaped/dotfiles/code.yml": dropped})
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 0 and [r["action"] for r in rows] == ["deleted"]
    assert (user_home / "Projects/app/README").exists()
    assert "~/Projects/app" not in states()
