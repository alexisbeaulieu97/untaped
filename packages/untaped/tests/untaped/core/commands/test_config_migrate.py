"""``untaped config migrate`` gives renamed and retired keys their new names."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, Field

from core_test_support.commands.support import compose, make_spec, write_config
from untaped.core.commands.config import build_root_config_app
from untaped.runtime import bootstrap
from untaped.sdk.health.deprecated_keys import Retired
from untaped.sdk.marks import deprecated
from untaped.sdk.storage._refs import RefHolder
from untaped.sdk.testing import CliInvoker, CliResult

pytestmark = pytest.mark.usefixtures("_isolated_config")


class Sweep(BaseModel):
    parallel: int = 12


class MigratedProfile(BaseModel):
    """Section ``mig``: a chain through a retired key, a nested rename, a deprecated setting."""

    renamed_keys: ClassVar[dict[str, str]] = {
        "corpus_path": "cache_dir",
        "git_fetch_concurrency": "git_fetch_parallel",
        "probe_concurrency": "probe_parallel",
        "sweep.sync_concurrency": "sweep.parallel",
    }
    retired_keys: ClassVar[dict[str, str]] = {"ancient_path": "corpus_path"}

    cache_dir: str = "cache"
    git_fetch_parallel: int = 8
    probe_parallel: int = 8
    legacy: Annotated[bool, deprecated(replacement="cache_dir")] = False
    sweep: Sweep = Field(default_factory=Sweep)


def _migrate(*args: str) -> CliResult:
    result = compose(make_spec("mig", settings=MigratedProfile))
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=result)
    return CliInvoker().invoke(app, ["migrate", *args])


ROUND_TRIP = """\
format_version: 2
# my config
mig:
  git_fetch_concurrency: 8   # fetches
  probe_concurrency: 8
  legacy: true
profiles:
  work:
    mig:
      ancient_path: '/old'  # oldest spelling
      sweep:
        sync_concurrency: 3
"""


def test_dry_run_plans_and_writes_nothing(_isolated_config: Path) -> None:
    write_config(_isolated_config, ROUND_TRIP)

    result = _migrate("--dry-run", "--format", "json")

    assert result.exit_code == 0, result.output
    assert {row["action"] for row in json.loads(result.stdout)} == {"planned"}
    assert "would rename 4 keys" in result.stderr
    assert _isolated_config.read_text() == ROUND_TRIP


@pytest.mark.parametrize("dry_run", [True, False])
def test_preview_and_apply_name_the_other_registered_installs(
    _isolated_config: Path, tmp_path: Path, dry_run: bool
) -> None:
    from untaped.sdk.testing import write_reference

    write_config(_isolated_config, ROUND_TRIP)
    other = tmp_path / "checkout"
    (other / "untaped-11.1.0.dist-info").mkdir(parents=True)
    write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"), [], anchor=other, version="11.1.0"
    )
    write_reference(
        RefHolder(kind="install", name="bbbbbbbbbbbb"),
        [],
        anchor=tmp_path / "gone",
        version="11.0.0",
    )

    result = _migrate(*(["--dry-run"] if dry_run else []), "--format", "json")

    assert result.exit_code == 0, result.stderr
    assert (
        f"other untaped installs here may still read the old keys (untaped 11.1.0 at {other}): "
        "migrate once every install is upgraded"
    ) in result.stderr
    assert "11.0.0" not in result.stderr  # an install that is gone reads nothing


def test_apply_renames_every_profile_and_keeps_comments_and_order(_isolated_config: Path) -> None:
    write_config(_isolated_config, ROUND_TRIP)

    result = _migrate("--format", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "profile": "default",
            "from": "mig.git_fetch_concurrency",
            "to": "mig.git_fetch_parallel",
            "action": "renamed",
            "detail": "",
        },
        {
            "profile": "default",
            "from": "mig.probe_concurrency",
            "to": "mig.probe_parallel",
            "action": "renamed",
            "detail": "",
        },
        {
            "profile": "work",
            "from": "mig.ancient_path",
            "to": "mig.cache_dir",
            "action": "renamed",
            "detail": "",
        },
        {
            "profile": "work",
            "from": "mig.sweep.sync_concurrency",
            "to": "mig.sweep.parallel",
            "action": "renamed",
            "detail": "",
        },
    ]
    assert "renamed 4 keys" in result.stderr
    # Comments keep their column, so a shorter key gains padding before one.
    assert _isolated_config.read_text() == (
        ROUND_TRIP.replace("git_fetch_concurrency: 8   #", "git_fetch_parallel: 8      #")
        .replace("probe_concurrency", "probe_parallel")
        .replace("ancient_path: '/old'  #", "cache_dir: '/old'     #")
        .replace("sync_concurrency", "parallel")
    )


@pytest.mark.parametrize(
    ("text", "dropped", "kept"),
    [
        ("corpus_path: /a\n  cache_dir: /b\n", "mig.corpus_path", "mig.cache_dir"),
        ("ancient_path: /a\n  corpus_path: /b\n", "mig.ancient_path", "mig.corpus_path"),
    ],
)
def test_a_redundant_spelling_is_dropped(
    _isolated_config: Path, text: str, dropped: str, kept: str
) -> None:
    write_config(_isolated_config, f"format_version: 2\nmig:\n  {text}")

    result = _migrate("--format", "json")

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert {
        "profile": "default",
        "from": dropped,
        "to": kept,
        "action": "dropped",
        "detail": "",
    } in rows
    assert "dropped 1" in result.stderr
    assert "mig:\n  cache_dir: /b\n" in _isolated_config.read_text()


def test_state_and_deprecated_settings_are_left_alone(_isolated_config: Path) -> None:
    state = _isolated_config.parent / "state.yml"
    state.write_text("mig:\n  corpus_path: /s\n")
    write_config(_isolated_config, "format_version: 2\nmig:\n  legacy: true\n")

    result = _migrate()

    assert result.exit_code == 0, result.output
    assert "no renamed, retired or deleted keys in the config" in result.stderr
    assert state.read_text() == "mig:\n  corpus_path: /s\n"
    assert "legacy: true" in _isolated_config.read_text()


def test_nothing_to_do_is_an_empty_list(_isolated_config: Path) -> None:
    result = _migrate("--format", "json")

    assert (result.exit_code, json.loads(result.stdout)) == (0, [])


def test_rows_carry_the_migration_outcome_kind(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nmig:\n  corpus_path: /c\n")

    result = _migrate("--format", "pipe")

    assert {json.loads(line)["kind"] for line in result.stdout.splitlines()} == {
        "untaped.config_migration_outcome"
    }


class Nested(BaseModel):
    parallel: int = 4


class BlockedProfile(BaseModel):
    """Section ``blk``: an old key whose new parent may not be a mapping."""

    renamed_keys: ClassVar[dict[str, str]] = {"workers": "sweep.parallel"}

    sweep: Nested = Field(default_factory=Nested)


def test_a_key_that_cannot_be_placed_stays_and_other_profiles_still_migrate(
    _isolated_config: Path,
) -> None:
    text = (
        "format_version: 2\nblk:\n  workers: 3\n  sweep: 5\n"
        "profiles:\n  empty:\n  work:\n    blk:\n      workers: 2\n"
    )
    write_config(_isolated_config, text)
    result = compose(make_spec("blk", settings=BlockedProfile))
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=result)

    migrated = CliInvoker().invoke(app, ["migrate", "--format", "json"])

    assert migrated.exit_code == 0, migrated.output
    assert json.loads(migrated.stdout) == [
        {
            "profile": "work",
            "from": "blk.workers",
            "to": "blk.sweep.parallel",
            "action": "renamed",
            "detail": "",
        }
    ]
    assert "blk:\n  workers: 3\n  sweep: 5\n" in _isolated_config.read_text()


class DeletingProfile(BaseModel):
    """Section ``del``: a deleted key, a rename chain ending at it, and a live rename."""

    retired_keys: ClassVar[dict[str, str | Retired]] = {
        "corpus_path": "cache_dir",
        "cache_dir": Retired(note="deleted in 11.0; the store lives under git.store_dir"),
        "protocol": Retired(note="deleted in 11.0; set github.git_protocol"),
        "old_parallel": "parallel",
    }

    parallel: int = 4


def _migrate_deleting(*args: str) -> CliResult:
    result = compose(make_spec("del", settings=DeletingProfile))
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=result)
    return CliInvoker().invoke(app, ["migrate", *args])


DELETING = """format_version: 2
del:
  corpus_path: ~/corpus
  protocol: ssh
  old_parallel: 2
  symbols:
    a: b
"""


def test_the_preview_prints_each_deleted_value_and_why(_isolated_config: Path) -> None:
    write_config(_isolated_config, DELETING)

    result = _migrate_deleting("--dry-run", "--format", "json")

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[:2] == [
        {
            "profile": "default",
            "from": "del.corpus_path",
            "to": "",
            "action": "planned",
            "detail": "was ~/corpus; deleted in 11.0 (via cache_dir); "
            "the store lives under git.store_dir",
        },
        {
            "profile": "default",
            "from": "del.protocol",
            "to": "",
            "action": "planned",
            "detail": "was ssh; deleted in 11.0; set github.git_protocol",
        },
    ]
    assert "would rename 1 key, delete 2" in result.stderr
    assert _isolated_config.read_text() == DELETING


def test_apply_deletes_the_keys(_isolated_config: Path) -> None:
    write_config(_isolated_config, DELETING)

    result = _migrate_deleting("--format", "json")

    assert result.exit_code == 0, result.output
    assert [row["action"] for row in json.loads(result.stdout)] == [
        "deleted",
        "deleted",
        "renamed",
    ]
    assert "renamed 1 key, deleted 2" in result.stderr
    assert _isolated_config.read_text() == (
        "format_version: 2\ndel:\n  parallel: 2\n  symbols:\n    a: b\n"
    )


def test_only_deletes_lead_the_summary(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\ndel:\n  cache_dir:\n    a: 1\n")

    result = _migrate_deleting("--dry-run", "--format", "json")

    (row,) = json.loads(result.stdout)
    assert row["detail"].startswith("was <mapping>; deleted in 11.0;")
    assert "would delete 1 key" in result.stderr


def test_drops_only_the_sections_it_empties(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\n"
        "del:\n  protocol: ssh\n"
        "profiles:\n  other:\n    del: {}\n"
        "  work:\n    del:\n      protocol: ssh\n      parallel: 3\n",
    )

    result = _migrate_deleting("--format", "json")

    assert result.exit_code == 0, result.output
    assert _isolated_config.read_text() == (
        "format_version: 2\nprofiles:\n  other:\n    del: {}\n"
        "  work:\n    del:\n      parallel: 3\n"
    )
