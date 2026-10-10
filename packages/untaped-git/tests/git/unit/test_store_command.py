"""`untaped git store` and the `git.store` doctor row read the store from disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.testing import CliInvoker, plugin_candidate
from untaped_git import SPEC


def _root() -> object:
    return bootstrap.build_root_app(candidates=(plugin_candidate(SPEC),)).meta


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "store"
    monkeypatch.setenv("UNTAPED_GIT__STORE_DIR", str(root))
    return root


def _repo(root: Path, name: str, *, config: str = "") -> Path:
    repo = root / "git.example" / name
    (repo / "objects" / "pack").mkdir(parents=True)
    (repo / "config").write_text(config, encoding="utf-8")
    return repo


def _store_row() -> dict[str, object]:
    result = CliInvoker().invoke(_root(), ["doctor", "--format", "json"])  # type: ignore[arg-type]
    rows = {str(row["check"]): row for row in json.loads(result.stdout)}
    return rows["git.store"]


def test_a_healthy_store_passes(store: Path) -> None:
    _repo(store, "a.git")
    row = _store_row()
    assert row["status"] == "pass"
    assert str(row["detail"]).startswith("1 repo, ")


def test_paused_maintenance_and_an_interrupted_release_warn(store: Path) -> None:
    (_repo(store, "a.git") / "gc.log").write_text("error\n", encoding="utf-8")
    _repo(store, "b.git", config="[untaped]\n\trelease = github\n")

    row = _store_row()

    assert row["status"] == "warn"
    assert row["detail"] == (
        "1 repo with gc.log (auto maintenance paused); "
        "1 unowned repo (an interrupted release; finished on next use)"
    )
    assert row["fix"] is None


def test_the_command_prints_lines_or_the_record(store: Path) -> None:
    repo = _repo(store, "a.git")
    (repo / "untaped-github.json").write_text("{}")

    table = CliInvoker().invoke(_root(), ["git", "store"])  # type: ignore[arg-type]
    data = CliInvoker().invoke(_root(), ["git", "store", "--format", "json"])  # type: ignore[arg-type]

    assert table.exit_code == 0, table.output
    assert [line.split("  ")[0] for line in table.stdout.splitlines()] == [
        "store",
        "used by",
        "exclusive",
        "packs",
    ]
    (record,) = (
        json.loads(data.stdout) if data.stdout.startswith("[") else [json.loads(data.stdout)]
    )
    assert record["used_by"] == {"github": 1}
