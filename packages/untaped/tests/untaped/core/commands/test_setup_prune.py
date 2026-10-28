"""``untaped setup prune``: dead install references and the folders of plugins not here."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core_test_support.commands.support import ghost_candidates, make_plugin
from untaped.core.commands import setup_prune
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate, named_plugin_dir
from untaped.sdk.storage import _refs as refs
from untaped.sdk.storage._refs import RefHolder, RefTarget
from untaped.sdk.testing import (
    CliResult,
    ScriptedPromptBackend,
    invoke_cli,
    plugin_candidate,
    write_reference,
)
from untaped.sdk.words import shown_path

pytestmark = pytest.mark.usefixtures("home_run")


def _cli(*args: str, also: tuple[PluginCandidate, ...] = ()) -> CliResult:
    root = bootstrap.build_root_app(candidates=[plugin_candidate(make_plugin("acme")), *also])
    return invoke_cli(root.meta, ["setup", "prune", *args])


def _rows(result: CliResult) -> list[dict[str, Any]]:
    assert result.exit_code == 0, result.output
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return rows


def _folder(plugin: str) -> Path:
    folder = named_plugin_dir(plugin)
    (folder / "cached" / "items").mkdir(parents=True)
    (folder / "cached" / "items" / "k.json").write_bytes(b"x" * 5)
    (folder / "state.yml").write_bytes(b"y" * 3)
    return folder


def _dead(tmp_path: Path, name: str = "aaaaaaaaaaaa") -> Path:
    return write_reference(
        RefHolder(kind="install", name=name),
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=tmp_path / "gone",
        version="11.0.0",
    )


def _live(tmp_path: Path, *targets: RefTarget, plugins: tuple[str, ...] | None = ()) -> Path:
    alive = tmp_path / "tool"
    (alive / "untaped-12.0.0.dist-info").mkdir(parents=True, exist_ok=True)
    return write_reference(
        RefHolder(kind="install", name="bbbbbbbbbbbb"),
        list(targets),
        anchor=alive,
        version="12.0.0",
        plugins=plugins,
    )


def test_dry_run_lists_both_kinds_and_removes_nothing(tmp_path: Path) -> None:
    dead = _dead(tmp_path)
    gone = _folder("gone")

    rows = _rows(_cli("--dry-run", "-f", "json"))

    assert rows == [
        {
            "what": "reference",
            "action": "planned",
            "detail": f"untaped 11.0.0 at {tmp_path / 'gone'}: not reachable",
            "bytes": dead.stat().st_size,
            "target_path": str(dead),
        },
        {
            "what": "folder",
            "action": "planned",
            "detail": (
                "plugin 'gone' isn't installed here and no install sharing this home uses it: "
                "the whole folder goes, its state included (8 B)"
            ),
            "bytes": 8,
            "target_path": str(gone),
        },
    ]
    assert dead.exists() and gone.exists()


def test_yes_removes_both_and_keeps_installed_plugins_folders(tmp_path: Path) -> None:
    dead = _dead(tmp_path)
    gone = _folder("gone")
    mine = _folder("acme")

    result = _cli("--yes", "-f", "json")

    assert [row["action"] for row in _rows(result)] == ["removed", "removed"]
    assert "removed 1 reference and 1 plugin folder" in result.stderr
    assert not dead.exists() and not gone.exists()
    assert mine.exists() and named_plugin_dir("untaped").parent.exists()


def test_a_terminal_asks_once_after_the_preview(tmp_path: Path) -> None:
    _dead(tmp_path)
    gone = _folder("gone")
    backend = ScriptedPromptBackend(confirms=[True])
    root = bootstrap.build_root_app(candidates=[plugin_candidate(make_plugin("acme"))])

    result = invoke_cli(root.meta, ["setup", "prune"], interactive=True, prompt_backend=backend)

    assert result.exit_code == 0, result.output
    assert backend.calls == [("confirm", "Remove 1 reference and 1 plugin folder?")]
    assert not gone.exists()


def test_without_a_terminal_it_needs_yes(tmp_path: Path) -> None:
    dead = _dead(tmp_path)
    gone = _folder("gone")

    result = _cli()

    assert result.exit_code == 2
    assert 'requires --yes when not interactive: it asks "Remove 1 reference and 1' in (
        result.stderr
    )
    assert dead.exists() and gone.exists()


def test_nothing_to_prune_says_so() -> None:
    result = _cli("--yes", "-f", "json")

    assert _rows(result) == []
    assert "nothing to prune" in result.stderr


def test_a_symlinked_folder_is_kept_with_its_reason(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    (shared / "cached").mkdir(parents=True)
    link = named_plugin_dir("gone")
    link.parent.mkdir(parents=True)
    link.symlink_to(shared)

    result = _cli("--yes", "-f", "json")

    [row] = _rows(result)
    assert (row["action"], row["detail"]) == ("kept", f"a symlink to {shared}: it stays")
    assert "nothing to prune" in result.stderr
    assert link.is_symlink() and (shared / "cached").is_dir()


def test_a_live_reference_is_kept_and_keeps_the_folder_it_names(tmp_path: Path) -> None:
    live = _live(tmp_path, RefTarget(plugin="gone", store="data", layout=1))
    gone = _folder("gone")

    [row] = _rows(_cli("--yes", "-f", "json"))

    assert row["what"] == "folder" and row["action"] == "kept"
    assert row["detail"] == f"untaped 12.0.0 at {tmp_path / 'tool'} uses it"
    assert live.exists() and gone.exists()


def test_another_live_install_composing_the_plugin_keeps_its_folder(tmp_path: Path) -> None:
    """Install B composes jira and names no jira target: its plugins/jira/ still stays."""
    _live(tmp_path, RefTarget(plugin="acme", store="data", layout=4), plugins=("acme", "jira"))
    jira = _folder("jira")

    [row] = _rows(_cli("--yes", "-f", "json"))

    assert (row["action"], row["detail"]) == (
        "kept",
        f"untaped 12.0.0 at {tmp_path / 'tool'} uses it",
    )
    assert (jira / "state.yml").exists()


def test_a_live_install_that_does_not_list_its_plugins_keeps_every_folder(
    tmp_path: Path,
) -> None:
    """Its file has no ``plugins`` key (unlike an empty list): it may compose any of them."""
    file = _live(tmp_path, plugins=None)
    jira = _folder("jira")

    [row] = _rows(_cli("--yes", "-f", "json"))

    assert (row["action"], row["detail"]) == (
        "kept",
        f"untaped 12.0.0 at {tmp_path / 'tool'} may use it: {shown_path(file)} doesn't list "
        "the plugins it composes; upgrade that install, or delete that file if the install "
        "is gone",
    )
    assert (jira / "state.yml").exists()


def test_a_folder_no_live_install_names_is_removed(tmp_path: Path) -> None:
    _live(tmp_path, plugins=("acme", "github"))
    _dead(tmp_path).unlink()
    write_reference(  # an install that is not reachable composing jira names nothing
        RefHolder(kind="install", name="cccccccccccc"),
        [],
        anchor=tmp_path / "unmounted",
        version="12.0.0",
        plugins=("jira",),
    )
    jira = _folder("jira")

    rows = _rows(_cli("--yes", "-f", "json"))

    assert [(row["what"], row["action"]) for row in rows] == [
        ("reference", "removed"),
        ("folder", "removed"),
    ]
    assert not jira.exists()


def test_a_busy_references_lock_fails_every_item_and_removes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from filelock import FileLock

    dead = _dead(tmp_path)
    gone = _folder("gone")
    monkeypatch.setattr(refs.REFS, "wait", timedelta(seconds=0.01))
    with FileLock(str(refs.REFS.lock_path())):
        result = _cli("--yes", "-f", "json")

    assert result.exit_code == 5, result.output
    assert [row["action"] for row in json.loads(result.stdout)] == ["failed", "failed"]
    assert "untaped.refs is busy" in result.stderr
    assert dead.exists() and gone.exists()


def test_a_folder_whose_real_place_is_elsewhere_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction or bind mount: not a symlink, but its real place is not plugins/<name>."""
    gone = _folder("gone")
    real = setup_prune.os.path.realpath
    elsewhere = str(tmp_path / "elsewhere")

    def realpath(path: Any, *args: Any, **kwargs: Any) -> str:
        return elsewhere if Path(path) == gone else real(path, *args, **kwargs)

    monkeypatch.setattr(setup_prune.os.path, "realpath", realpath)
    [row] = _rows(_cli("--yes", "-f", "json"))

    assert row["action"] == "kept" and row["detail"].startswith("not in ")
    assert gone.exists()


def test_an_unreadable_reference_keeps_every_folder() -> None:
    gone = _folder("gone")
    refs.REFS.place.mkdir(parents=True, exist_ok=True)
    broken = refs.REFS.place / "install-cccccccccccc.json"
    broken.write_text("{", encoding="utf-8")

    [row] = _rows(_cli("--yes", "-f", "json"))

    assert row["action"] == "kept" and row["detail"].startswith(
        "another install sharing this home may use it: "
    )
    assert gone.exists() and broken.exists()


def test_a_quarantined_plugins_folder_and_cores_are_never_offered() -> None:
    _folder("ghost")
    ghosts = (*ghost_candidates("one-dist", "other-dist"),)
    (named_plugin_dir("untaped") / "cached").mkdir(parents=True, exist_ok=True)
    (named_plugin_dir("untaped").parent / ".trash").mkdir()

    assert _rows(_cli("--yes", "-f", "json", also=ghosts)) == []
    assert named_plugin_dir("ghost").exists()


def test_apply_checks_each_item_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reference written again and a folder a new reference names, since the preview, stay."""
    dead = _dead(tmp_path)
    gone = _folder("gone")
    removed = setup_prune._removed

    def changed(rows: Any, diag: Any) -> Any:
        (tmp_path / "gone" / "untaped-12.0.0.dist-info").mkdir(parents=True)
        _live(tmp_path, RefTarget(plugin="gone", store="data", layout=1))
        return removed(rows, diag)

    monkeypatch.setattr(setup_prune, "_removed", changed)
    rows = _rows(_cli("--yes", "-f", "json"))

    assert [(row["what"], row["action"]) for row in rows] == [
        ("reference", "skipped"),
        ("folder", "kept"),
    ]
    assert rows[0]["detail"].endswith("written again since the preview, so it stays")
    assert dead.exists() and gone.exists()


def test_one_failure_fails_the_run_and_the_rest_still_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dead = _dead(tmp_path)
    stuck = _folder("stuck")
    gone = _folder("gone")
    real = setup_prune.shutil.rmtree

    def rmtree(path: Path, *args: Any, **kwargs: Any) -> None:
        if Path(path) == stuck:
            raise PermissionError(13, "Permission denied")
        real(path, *args, **kwargs)

    monkeypatch.setattr(setup_prune.shutil, "rmtree", rmtree)
    result = _cli("--yes", "-f", "json")

    assert result.exit_code == 1, result.output
    rows = {Path(row["target_path"]).name: row for row in json.loads(result.stdout)}
    assert rows["stuck"]["action"] == "failed"
    assert rows["stuck"]["error"]["message"].endswith("Permission denied")
    assert rows["gone"]["action"] == rows[dead.name]["action"] == "removed"
    assert stuck.exists() and not gone.exists() and not dead.exists()


def test_doctor_points_both_rows_at_prune(tmp_path: Path) -> None:
    _dead(tmp_path)
    _folder("gone")
    root = bootstrap.build_root_app(candidates=[plugin_candidate(make_plugin("acme"))])

    result = invoke_cli(root.meta, ["doctor", "-f", "json"])

    rows = {row["check"]: row for row in json.loads(result.stdout)}
    for check in ("references", "plugin-folders"):
        assert rows[check]["status"] == "warn"
        assert rows[check]["fix"] == ["--profile", "default", "setup", "prune"]
        assert rows[check]["automatic"] is False
