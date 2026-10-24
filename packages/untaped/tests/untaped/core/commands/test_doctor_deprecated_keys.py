"""The doctor ``deprecated-keys`` row lists old keys in every profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, ClassVar

import pytest
from pydantic import BaseModel, Field

from core_test_support.commands.support import compose, make_spec, write_config
from untaped.core.commands.doctor import build_root_doctor_app, selected_check_rows
from untaped.runtime import bootstrap
from untaped.sdk.health.deprecated_keys import Retired
from untaped.sdk.marks import deprecated
from untaped.sdk.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


class Sweep(BaseModel):
    parallel: int = 12


class OldProfile(BaseModel):
    """Section ``old``: renamed, nested renamed, retired and deleted keys, a deprecated setting."""

    renamed_keys: ClassVar[dict[str, str]] = {
        "corpus_path": "cache_dir",
        "sweep.sync_concurrency": "sweep.parallel",
    }
    retired_keys: ClassVar[dict[str, str | Retired]] = {
        "repo_path": "cache_dir",
        "store_dir": Retired(note="deleted in 11.0; set git.store_dir"),
    }

    cache_dir: str = "cache"
    legacy: Annotated[bool, deprecated(replacement="cache_dir")] = False
    sweep: Sweep = Field(default_factory=Sweep)


def _doctor(*args: str) -> Any:
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC,
        builtin_for=lambda _name: None,
        result=compose(make_spec("old", settings=OldProfile)),
    )
    return CliInvoker().invoke(app, list(args))  # type: ignore[arg-type]


def _rows(**checks: str) -> dict[str, dict[str, Any]]:
    result = _doctor("--format", "json")
    assert result.exit_code == 0, result.output
    return {row["check"]: row for row in json.loads(result.stdout) if row["check"] in checks}


def test_old_keys_in_every_profile_are_listed_with_the_migrate_fix(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nold:\n  corpus_path: /c\n  legacy: true\n"
        "profiles:\n  work:\n    old:\n      repo_path: /r\n"
        "      sweep:\n        sync_concurrency: 2\n",
    )

    rows = _rows(**{"deprecated-keys": "", "unknown-keys": ""})

    row = rows["deprecated-keys"]
    assert (row["status"], row["title"]) == ("warn", "deprecated config keys")
    assert row["detail"] == (
        "old.corpus_path (profile default, removed in a future major release) "
        "→ old.cache_dir; "
        "old.legacy (profile default, removed in a future major release): use cache_dir; "
        "old.repo_path (profile work, retired) → old.cache_dir; "
        "old.sweep.sync_concurrency (profile work, removed in a future major release) "
        "→ old.sweep.parallel"
    )
    assert (row["fix"], row["automatic"]) == (["--profile", "default", "config", "migrate"], True)
    assert rows["unknown-keys"]["status"] == "pass"


def test_the_fix_names_the_active_profile(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nprofiles:\n  work:\n    old:\n      corpus_path: /c\n",
    )
    monkeypatch.setenv("UNTAPED_PROFILE", "work")

    row = _rows(**{"deprecated-keys": ""})["deprecated-keys"]

    assert row["fix"] == ["--profile", "work", "config", "migrate"]


def test_the_table_shows_the_automatic_fix(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nold:\n  corpus_path: /c\n")

    result = _doctor("--format", "table")

    assert "→ untaped config migrate  (automatic)" in result.stdout


def test_deprecated_settings_alone_have_no_fix(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nold:\n  legacy: true\n")

    row = _rows(**{"deprecated-keys": ""})["deprecated-keys"]

    assert (row["status"], row["fix"], row["automatic"]) == ("warn", None, False)


def test_no_old_keys_pass(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nold:\n  cache_dir: /c\n")

    row = _rows(**{"deprecated-keys": ""})["deprecated-keys"]

    assert (row["status"], row["detail"]) == ("pass", "no deprecated keys")


def test_setup_keeps_a_warning_row(_isolated_config: Path) -> None:
    result = compose(make_spec("old", settings=OldProfile))

    def checks() -> set[str]:
        rows = selected_check_rows(bootstrap.SHELL_SPEC, result, "default", frozenset({"old"}))
        return {str(row["check"]) for row in rows if row["plugin"] != "old"}

    write_config(_isolated_config, "format_version: 2\n")
    assert checks() == set()
    write_config(_isolated_config, "format_version: 2\nold:\n  corpus_path: /c\n")
    assert checks() == {"deprecated-keys"}


def test_a_deleted_key_is_unknown_with_its_note_and_the_migrate_fix(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nold:\n  store_dir: /s\n  typo: 1\n",
    )

    rows = _rows(**{"deprecated-keys": "", "unknown-keys": ""})

    row = rows["unknown-keys"]
    assert row["status"] == "warn"
    assert row["detail"] == (
        "ignored: old.typo; old.store_dir (profile default): deleted in 11.0; set git.store_dir"
    )
    assert (row["fix"], row["automatic"]) == (["--profile", "default", "config", "migrate"], True)
    assert rows["deprecated-keys"]["status"] == "pass"


def test_a_retired_or_deleted_variable_still_set_is_named(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Retired keys are no longer read, so a variable left in a shell profile is silently ignored.
    write_config(_isolated_config, "format_version: 2\n")
    monkeypatch.setenv("UNTAPED_OLD__REPO_PATH", "/r")
    monkeypatch.setenv("untaped_old__store_dir", "/s")

    row = _rows(**{"deprecated-keys": ""})["deprecated-keys"]

    assert row["status"] == "warn"
    assert row["detail"] == (
        "UNTAPED_OLD__REPO_PATH (environment, retired) → UNTAPED_OLD__CACHE_DIR; "
        "UNTAPED_OLD__STORE_DIR (environment): deleted in 11.0; set git.store_dir"
    )
    # config migrate rewrites the config file, not the environment.
    assert (row["fix"], row["automatic"]) == (None, False)


def test_a_renamed_variable_is_left_to_the_read_warning(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "format_version: 2\n")
    monkeypatch.setenv("UNTAPED_OLD__CORPUS_PATH", "/c")

    row = _rows(**{"deprecated-keys": ""})["deprecated-keys"]

    assert row["status"] == "pass"
