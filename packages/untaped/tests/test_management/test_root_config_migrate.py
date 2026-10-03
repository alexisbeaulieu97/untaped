"""``untaped config migrate`` renames deprecated keys in every profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, Field

from test_management.support import compose, make_spec, write_config
from untaped import bootstrap
from untaped.management.config import build_root_config_app
from untaped.testing import CliInvoker, CliResult

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
    deprecated_settings: ClassVar[dict[str, str]] = {"legacy": "use cache_dir"}

    cache_dir: str = "cache"
    git_fetch_parallel: int = 8
    probe_parallel: int = 8
    legacy: bool = False
    sweep: Sweep = Field(default_factory=Sweep)


def _migrate(*args: str) -> CliResult:
    result = compose(make_spec("mig", profile_model=MigratedProfile))
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=result)
    return CliInvoker().invoke(app, ["migrate", *args])


ROUND_TRIP = """\
# my config
profiles:
  default:
    mig:
      git_fetch_concurrency: 8   # fetches
      probe_concurrency: 8
      legacy: true
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
        },
        {
            "profile": "default",
            "from": "mig.probe_concurrency",
            "to": "mig.probe_parallel",
            "action": "renamed",
        },
        {"profile": "work", "from": "mig.ancient_path", "to": "mig.cache_dir", "action": "renamed"},
        {
            "profile": "work",
            "from": "mig.sweep.sync_concurrency",
            "to": "mig.sweep.parallel",
            "action": "renamed",
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
        ("corpus_path: /a\n      cache_dir: /b\n", "mig.corpus_path", "mig.cache_dir"),
        ("ancient_path: /a\n      corpus_path: /b\n", "mig.ancient_path", "mig.corpus_path"),
    ],
)
def test_a_redundant_spelling_is_dropped(
    _isolated_config: Path, text: str, dropped: str, kept: str
) -> None:
    write_config(_isolated_config, f"profiles:\n  default:\n    mig:\n      {text}")

    result = _migrate("--format", "json")

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert {"profile": "default", "from": dropped, "to": kept, "action": "dropped"} in rows
    assert "dropped 1" in result.stderr
    assert "mig:\n      cache_dir: /b\n" in _isolated_config.read_text()


def test_state_and_deprecated_settings_are_left_alone(_isolated_config: Path) -> None:
    state = _isolated_config.parent / "state.yml"
    state.write_text("mig:\n  corpus_path: /s\n")
    write_config(_isolated_config, "profiles:\n  default:\n    mig:\n      legacy: true\n")

    result = _migrate()

    assert result.exit_code == 0, result.output
    assert "no deprecated keys in the config" in result.stderr
    assert state.read_text() == "mig:\n  corpus_path: /s\n"
    assert "legacy: true" in _isolated_config.read_text()


def test_nothing_to_do_is_an_empty_list(_isolated_config: Path) -> None:
    result = _migrate("--format", "json")

    assert (result.exit_code, json.loads(result.stdout)) == (0, [])


def test_rows_carry_the_migration_outcome_kind(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    mig:\n      corpus_path: /c\n")

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
        "profiles:\n  default:\n    blk:\n      workers: 3\n      sweep: 5\n"
        "  empty:\n  work:\n    blk:\n      workers: 2\n"
    )
    write_config(_isolated_config, text)
    result = compose(make_spec("blk", profile_model=BlockedProfile))
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=result)

    migrated = CliInvoker().invoke(app, ["migrate", "--format", "json"])

    assert migrated.exit_code == 0, migrated.output
    assert json.loads(migrated.stdout) == [
        {"profile": "work", "from": "blk.workers", "to": "blk.sweep.parallel", "action": "renamed"}
    ]
    assert "workers: 3\n      sweep: 5\n" in _isolated_config.read_text()
