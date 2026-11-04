"""Working copies: an item's ``repos:`` entries are cloned once and then left to the person."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

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
    assert code == 0 and rows[0]["detail"] == f"clones {app.url} into ~/Projects/app"
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
    assert run(["status", "--check"]).exit_code == 3
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
    gone = make_remote("gone", packages={}, files={"README": "x\n"})
    shutil.rmtree(gone.path)  # its URL still maps to the local path: no network is asked
    _subscribed(make_remote, gone.url)
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
    assert code == 0 and [r["action"] for r in rows] == ["unchanged"]
    assert rows[0]["detail"] == (
        "the package no longer has it; forgotten; the working copy stays, it is yours"
    )
    assert (user_home / "Projects/app/README").exists()
    assert "~/Projects/app" not in states()


def test_a_moved_target_clones_anew_and_says_so(make_remote: MakeRemote, user_home: Path) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    _subscribed(
        make_remote,
        app.url,
        target="~/{{ dir }}/app",
        extra="inputs:\n  dir: {description: Where projects go}\n",
    )
    assert _repo_rows(["apply", "--yes", "--var", "dir=one"])[0] == 0
    code, rows = _repo_rows(["apply", "--yes", "--var", "dir=two"])
    assert code == 0
    assert {r["entry"]: r["action"] for r in rows} == {
        "~/two/app": "created",
        "~/one/app": "unchanged",
    }
    moved = next(r for r in rows if r["entry"] == "~/one/app")
    assert str(moved["detail"]).startswith("its target moved to ~/two/app")
    assert (user_home / "one/app/README").exists() and (user_home / "two/app/README").exists()


def test_a_repo_name_is_matched_offline_in_status(make_remote: MakeRemote, user_home: Path) -> None:
    folder = user_home / "Projects/app"
    folder.mkdir(parents=True)
    git(folder, "init", "-q")
    git(folder, "remote", "add", "origin", "https://github.com/acme/App.git")
    _subscribed(make_remote, "acme/app")
    status = run(["status", "--format", "json"])  # no repo source is installed: none is asked
    assert status.exit_code == 0, status.output
    assert {r["entry"]: r["state"] for r in json_rows(status)}["~/Projects/app"] == "applied"
    git(folder, "remote", "set-url", "origin", "https://github.com/acme/api.git")
    row = {r["entry"]: r for r in as_json(["status"])}["~/Projects/app"]
    assert row["state"] == "foreign" and "not acme/app" in str(row["detail"])


def test_a_repo_name_is_resolved_once_to_clone(
    make_remote: MakeRemote, user_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    asked: list[str] = []

    def resolve(name: str) -> SimpleNamespace:
        asked.append(name)
        return SimpleNamespace(url=app.url)

    monkeypatch.setattr(
        "untaped_dotfiles.repos.RepoFinder", lambda: SimpleNamespace(resolve=resolve)
    )
    _subscribed(make_remote, "app")
    assert states()["~/Projects/app"] == "pending" and asked == []
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 0 and rows[0]["action"] == "created"
    assert rows[0]["detail"] == f"cloned {app.url}"
    assert states()["~/Projects/app"] == "applied"
    assert asked == ["app"]  # the plan's one lookup; status reads the record


def test_a_branch_is_cloned(make_remote: MakeRemote, user_home: Path) -> None:
    app = make_remote("app", packages={}, files={"README": "main\n"})
    app.branch("dev")
    app.commit("README", "dev\n", branch="dev")
    package = _package(app.url).replace(
        'target: "~/Projects/app"', 'target: "~/Projects/app", branch: dev'
    )
    source = make_remote(packages={"code": package}, files={})
    subscribe(source.url, "--add", "code")
    _, rows = _repo_rows(["apply", "--dry-run"])
    assert rows[0]["detail"] == f"clones {app.url} (dev) into ~/Projects/app"
    assert _repo_rows(["apply", "--yes"])[0] == 0
    assert (user_home / "Projects/app/README").read_text() == "dev\n"


def test_files_placed_in_a_copy_s_target_keep_it_from_cloning(
    make_remote: MakeRemote, user_home: Path
) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    package = _package(app.url) + (
        "    files:\n      - {path: envrc, target: ~/Projects/app/.envrc, mode: copy}\n"
    )
    source = make_remote(packages={"code": package}, files={"envrc": "use nix\n"})
    subscribe(source.url, "--add", "code")
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 1 and rows[0]["action"] == "failed"
    assert "files are placed in ~/Projects/app (~/Projects/app/.envrc)" in str(rows[0]["detail"])
    assert not (user_home / "Projects/app/.git").exists()
    code, rows = _repo_rows(["status"])
    assert code == 1 and rows[0]["state"] == "error"


def test_copies_whose_targets_overlap_are_not_cloned(
    make_remote: MakeRemote, user_home: Path
) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    package = _package(app.url) + (f'      - {{url: "{app.url}", target: "~/Projects/app/sub"}}\n')
    source = make_remote(packages={"code": package}, files={})
    subscribe(source.url, "--add", "code")
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 1 and [r["action"] for r in rows] == ["failed", "failed"]
    assert "overlaps the working copy at ~/Projects/app/sub" in str(rows[0]["detail"])
    assert not (user_home / "Projects/app").exists()


def test_a_target_waiting_on_an_input_keeps_its_copy(
    make_remote: MakeRemote, user_home: Path
) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    _subscribed(
        make_remote,
        app.url,
        target="~/{{ dir }}/app",
        extra="inputs:\n  dir: {description: Where projects go}\n",
    )
    assert _repo_rows(["apply", "--yes", "--var", "dir=one"])[0] == 0
    ran = installed().cli(["config", "unset", "dotfiles.sources.dotfiles.packages.code.inputs.dir"])
    assert int(ran.exit) == 0, ran.stderr
    assert states()["~/one/app"] == "applied"
    code, rows = _repo_rows(["apply", "--yes"])
    assert code == 0 and [r["action"] for r in rows] == ["unchanged"]
    assert states()["~/one/app"] == "applied"


def test_a_name_no_repo_source_lists_fails_its_row(
    make_remote: MakeRemote, user_home: Path
) -> None:
    _subscribed(make_remote, "acme/app")
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 1, result.output
    row = next(r for r in json_rows(result) if r["kind"] == "repo")
    assert row["action"] == "failed"
    assert row["error"]["category"] == "not_found"  # type: ignore[index]


def test_a_copy_inside_another_package_s_copy_is_not_cloned(
    make_remote: MakeRemote, user_home: Path
) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    source = make_remote(
        packages={
            "outer": _package(app.url),
            "inner": _package(app.url, target="~/Projects/app/sub"),
        },
        files={},
    )
    subscribe(source.url, "--add", "outer")
    assert _repo_rows(["apply", "--yes"])[0] == 0
    assert run(["add", "inner"]).exit_code == 0
    code, rows = _repo_rows(["apply", "inner", "--yes"])
    assert code == 1 and rows[0]["action"] == "failed"
    assert "overlaps the working copy at ~/Projects/app" in str(rows[0]["detail"])
    assert not (user_home / "Projects/app/sub").exists()
    sub = user_home / "Projects/app/sub"  # nested by hand: both there, neither an error
    sub.mkdir()
    git(sub, "init", "-q")
    git(sub, "remote", "add", "origin", app.url)
    found = states()
    assert found["~/Projects/app"] == found["~/Projects/app/sub"] == "applied"


def test_a_file_excluded_here_doesn_t_hold_a_copy_back(
    make_remote: MakeRemote, user_home: Path
) -> None:
    app = make_remote("app", packages={}, files={"README": "app\n"})
    package = _package(app.url) + (
        "    files:\n      - {path: envrc, target: ~/Projects/app/.envrc, os: [windows]}\n"
    )
    source = make_remote(packages={"code": package}, files={"envrc": "use nix\n"})
    subscribe(source.url, "--add", "code")
    assert states(["--excluded"])["~/Projects/app"] == "pending"
    assert _repo_rows(["apply", "--yes"])[0] == 0
