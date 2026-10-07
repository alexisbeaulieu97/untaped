"""The doctor ``deprecated-keys`` row lists old keys in every profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, ClassVar

import pytest
from pydantic import BaseModel, Field

from test_management.support import compose, make_spec, write_config
from untaped import bootstrap
from untaped.management.doctor import build_root_doctor_app, selected_check_rows
from untaped.stability import deprecated
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


class Sweep(BaseModel):
    parallel: int = 12


class OldProfile(BaseModel):
    """Section ``old``: a renamed, a nested renamed, a retired key and a deprecated setting."""

    renamed_keys: ClassVar[dict[str, str]] = {
        "corpus_path": "cache_dir",
        "sweep.sync_concurrency": "sweep.parallel",
    }
    retired_keys: ClassVar[dict[str, str]] = {"repo_path": "cache_dir"}

    cache_dir: str = "cache"
    legacy: Annotated[bool, deprecated(replacement="cache_dir")] = False
    sweep: Sweep = Field(default_factory=Sweep)


def _doctor(*args: str) -> Any:
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC,
        builtin_for=lambda _name: None,
        result=compose(make_spec("old", profile_model=OldProfile)),
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
        "profiles:\n  default:\n    old:\n      corpus_path: /c\n      legacy: true\n"
        "  work:\n    old:\n      repo_path: /r\n      sweep:\n        sync_concurrency: 2\n",
    )

    rows = _rows(**{"deprecated-keys": "", "unknown-keys": ""})

    row = rows["deprecated-keys"]
    assert (row["status"], row["title"]) == ("warn", "deprecated config keys")
    assert row["detail"] == (
        "old.corpus_path (profile default) → old.cache_dir; "
        "old.legacy (profile default, deprecated): use cache_dir; "
        "old.repo_path (profile work, retired) → old.cache_dir; "
        "old.sweep.sync_concurrency (profile work) → old.sweep.parallel"
    )
    assert (row["fix"], row["automatic"]) == (["--profile", "default", "config", "migrate"], True)
    assert rows["unknown-keys"]["status"] == "pass"


def test_the_fix_names_the_active_profile(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "active: work\nprofiles:\n  default: {}\n  work:\n    old:\n      corpus_path: /c\n",
    )

    row = _rows(**{"deprecated-keys": ""})["deprecated-keys"]

    assert row["fix"] == ["--profile", "work", "config", "migrate"]


def test_the_table_shows_the_automatic_fix(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    old:\n      corpus_path: /c\n")

    result = _doctor("--format", "table")

    assert "→ untaped config migrate  (automatic)" in result.stdout


def test_deprecated_settings_alone_have_no_fix(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    old:\n      legacy: true\n")

    row = _rows(**{"deprecated-keys": ""})["deprecated-keys"]

    assert (row["status"], row["fix"], row["automatic"]) == ("warn", None, False)


def test_no_old_keys_pass(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    old:\n      cache_dir: /c\n")

    row = _rows(**{"deprecated-keys": ""})["deprecated-keys"]

    assert (row["status"], row["detail"]) == ("pass", "no deprecated keys")


def test_setup_keeps_a_warning_row(_isolated_config: Path) -> None:
    result = compose(make_spec("old", profile_model=OldProfile))

    def checks() -> set[str]:
        rows = selected_check_rows(bootstrap.SHELL_SPEC, result, "default", frozenset({"old"}))
        return {str(row["check"]) for row in rows if row["capability"] != "old"}

    write_config(_isolated_config, "profiles:\n  default: {}\n")
    assert checks() == set()
    write_config(_isolated_config, "profiles:\n  default:\n    old:\n      corpus_path: /c\n")
    assert checks() == {"deprecated-keys"}
