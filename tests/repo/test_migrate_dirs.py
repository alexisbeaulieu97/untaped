"""S30: ``untaped setup migrate-dirs`` on a 10.x layout, every first-party row composed.

The fixture is the six directories a 10.x user has: a workspace cache whose
repo has two workspace worktrees and a hand-added one, a sweep cache holding a
shallow copy of the same repo (with a sweep worktree) and a second repo, the
9.x sweep corpus and ansible cache, ansible's 10.x cache, and the 9.x
``repositories`` a pre-7.0 clone borrows from. The overlap in the other order
(workspace's row first) is ``untaped-git``'s ``test_adopt``.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.management.doctor import collect_doctor_rows
from untaped.plugins.registry import PluginCandidate
from untaped.testing import CliResult, invoke_cli
from untaped.testing.git import GitRemote, git_remote
from untaped_workspace.domain.models import RepoSpec, WorkspaceRecord
from untaped_workspace.infrastructure.state_store import StateWorkspaceStore

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")


def git(cwd: Path, *args: str, bare: bool = False) -> str:
    where = ["--git-dir", str(cwd)] if bare else ["-C", str(cwd)]
    result = subprocess.run(["git", *where, *args], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _untaped() -> Path:
    return Path.home() / ".untaped"


def _store(name: str) -> Path:
    return _untaped() / "plugins" / "git" / "store" / "git.example" / f"{name}.git"


def _plant(app: GitRemote, lib: GitRemote, tmp_path: Path) -> dict[str, Path]:
    home = _untaped()
    # workspace-cache: a full clone in the 10.x layout, worktrees on main and feature.
    cache = home / "workspace-cache" / "git.example" / "app.git"
    cache.parent.mkdir(parents=True)
    git(tmp_path, "init", "-q", "--bare", str(cache))
    git(cache, "remote", "add", "origin", app.url, bare=True)
    git(cache, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*", bare=True)
    git(cache, "config", "untaped.layout", "2", bare=True)
    git(cache, "fetch", "-q", "origin", bare=True)
    workspaces = home / "workspaces"
    trees = {"J-1": "main", "J-2": "feature"}
    for name, branch in trees.items():
        tree = workspaces / name / "app"
        git(cache, "worktree", "add", "-q", "-b", branch, str(tree), f"origin/{branch}", bare=True)
        git(tree, "branch", "-q", "-u", f"origin/{branch}")
        StateWorkspaceStore().create(
            WorkspaceRecord(
                name=name,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                repos=(RepoSpec(name="app", url=app.url, dir="app", branch=branch, base="main"),),
            )
        )
    hand = tmp_path / "mine"
    git(cache, "worktree", "add", "-q", "--detach", str(hand), "origin/main", bare=True)

    # github-cache: shallow copies, heads mirrored, a sweep worktree of app.
    sweep = home / "github-cache"
    for remote in (app, lib):
        repo = sweep / "git.example" / f"{remote.path.stem}.git"
        repo.parent.mkdir(parents=True, exist_ok=True)
        git(tmp_path, "clone", "-q", "--bare", "--depth=1", remote.url, str(repo))
        corpus = {"profile": "default", "fetched_at": "2026-01-01", "pushed_at": "2026-01-01"}
        (repo / "untaped-corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
        (repo.parent / f"{repo.name}.lock").write_text("", encoding="utf-8")
    sweep_tree = sweep / "worktrees" / "app-1"
    git(sweep / "git.example" / "app.git", "worktree", "add", "-q", "--detach", str(sweep_tree),
        "main", bare=True)  # fmt: skip

    # 9.x and ansible leftovers, and a pre-7.0 clone borrowing from repositories.
    for name in ("github-corpus", "ansible-repositories", "ansible-cache"):
        (home / name / "x").mkdir(parents=True)
        (home / name / "x" / "file").write_text("old", encoding="utf-8")
    lender = home / "repositories" / "git.example" / "lib.git"
    lender.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--bare", lib.url, str(lender))
    borrower = workspaces / "OLD" / "lib"
    git(tmp_path, "clone", "-q", "--reference", str(lender), lib.url, str(borrower))
    return {"hand": hand, "sweep": sweep_tree, "borrower": borrower, "lender": lender}


def _run(candidates: tuple[PluginCandidate, ...], *args: str) -> CliResult:
    root = bootstrap.build_root_app(candidates=candidates)
    return invoke_cli(root.meta, ["setup", "migrate-dirs", "--format", "json", *args])


def _doctor(candidates: tuple[PluginCandidate, ...]) -> dict[str, object]:
    result = bootstrap.compose_root(candidates=candidates)
    rows = collect_doctor_rows(bootstrap.SHELL_SPEC, result)
    (row,) = [row for row in rows if row["check"] == "migrate-dirs"]
    return row


def test_a_10x_layout_moves_into_the_store(
    first_party_candidates: tuple[PluginCandidate, ...], tmp_path: Path
) -> None:
    app = git_remote(tmp_path, "app")
    app.tag("v1")
    app.branch("feature")
    lib = git_remote(tmp_path, "lib")
    paths = _plant(app, lib, tmp_path)
    home = _untaped()
    assert _doctor(first_party_candidates)["status"] == "warn"

    # --dry-run: the preview, in registry order, and nothing changes.
    preview = _run(first_party_candidates, "--dry-run")
    assert preview.exit_code == 0, preview.stderr
    rows = json.loads(preview.stdout)
    assert [(row["id"], row["action"]) for row in rows] == [
        ("ansible.cache", "delete"),
        ("ansible.repositories", "delete"),
        ("github.cache", "move"),
        ("github.cache", "move"),
        ("github.cache", "then"),
        ("github.corpus", "delete"),
        ("workspace.cache", "move"),
        ("workspace.repositories", "keep"),
    ]
    assert "1 clone" in rows[-1]["detail"]
    assert (home / "workspace-cache").is_dir() and (home / "github-cache").is_dir()
    assert not _store("app").exists()

    # --yes: everything moves or goes, except repositories.
    applied = _run(first_party_candidates, "--yes")
    assert applied.exit_code == 0, applied.stderr
    outcomes = {row["id"]: row["action"] for row in json.loads(applied.stdout)}
    assert outcomes == {
        "ansible.cache": "deleted",
        "ansible.repositories": "deleted",
        "github.cache": "moved",
        "github.corpus": "deleted",
        "workspace.cache": "moved",
        "workspace.repositories": "unchanged",
    }
    for name in ("workspace-cache", "github-cache", "github-corpus", "ansible-cache",
                 "ansible-repositories"):  # fmt: skip
        assert not (home / name).exists(), name

    # app: workspace's full copy won; github's file copied in without its timestamps.
    repo = _store("app")
    assert not (repo / "shallow").exists()
    refs = git(repo, "for-each-ref", "--format=%(refname)", bare=True).splitlines()
    assert "refs/heads/main" in refs and "refs/heads/feature" in refs
    assert not [ref for ref in refs if ref.startswith("refs/untaped/github/")]
    assert json.loads((repo / "untaped-github.json").read_text()) == {"profile": "default"}
    assert json.loads((repo / "untaped-workspace.json").read_text()) == {"history": "partial"}
    assert git(repo, "config", "--local", "--get-all", "untaped.store", bare=True)
    shared = subprocess.run(
        ["git", "--git-dir", str(repo), "config", "--local", "--get-all", "remote.origin.fetch"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shared.stdout == ""
    for name, branch in (("J-1", "main"), ("J-2", "feature")):
        tree = home / "workspaces" / name / "app"
        assert git(tree, "config", "--worktree", "untaped.owner") == "workspace"
        assert git(tree, "rev-parse", "@{upstream}") == app.oid(branch)
        git(tree, "pull", "-q", "--ff-only")
    assert git(paths["hand"], "rev-parse", "HEAD") == app.oid("main")
    assert not paths["sweep"].exists()
    assert not (home / "plugins" / "github" / "worktrees" / "app-1").exists()

    # lib: github's shallow copy moved, refs in github's namespace.
    lib_refs = git(_store("lib"), "for-each-ref", "--format=%(refname)", bare=True).splitlines()
    assert lib_refs == ["refs/untaped/github/heads/main"]
    assert (_store("lib") / "untaped-github.json").is_file()

    # repositories stays for its borrower; --dissociate repacks it and deletes it.
    assert paths["lender"].is_dir()
    dissociated = _run(first_party_candidates, "--yes", "--dissociate")
    assert dissociated.exit_code == 0, dissociated.stderr
    by_id = {row["id"]: row for row in json.loads(dissociated.stdout)}
    assert by_id["workspace.repositories"]["action"] == "deleted"
    assert not (home / "repositories").exists()
    borrower = paths["borrower"]
    assert not (borrower / ".git" / "objects" / "info" / "alternates").exists()
    git(borrower, "fsck", "--connectivity-only")
    assert git(borrower, "rev-parse", "HEAD") == lib.oid("main")

    # Doctor passes, and a rerun has nothing to do.
    assert _doctor(first_party_candidates)["status"] == "pass"
    again = _run(first_party_candidates, "--yes")
    assert again.exit_code == 0
    assert json.loads(again.stdout) == []
