"""``config get|set|unset|list`` follow a capability's renamed keys."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, Field

from test_management.support import compose, make_spec, write_config
from untaped import bootstrap
from untaped.config_file import read_config_dict
from untaped.management.config import build_root_config_app
from untaped.testing import CliInvoker, CliResult

pytestmark = pytest.mark.usefixtures("_isolated_config")


class Sweep(BaseModel):
    parallel: int = 12


class RenamedProfile(BaseModel):
    """Section ``renamed``: one renamed key, one nested rename, one retired key."""

    renamed_keys: ClassVar[dict[str, str]] = {
        "corpus_path": "cache_dir",
        "sweep.sync_concurrency": "sweep.parallel",
    }
    retired_keys: ClassVar[dict[str, str]] = {"ancient_path": "cache_dir"}

    cache_dir: str = "cache"
    sweep: Sweep = Field(default_factory=Sweep)


def _config(*args: str) -> CliResult:
    result = compose(make_spec("renamed", profile_model=RenamedProfile))
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=result)
    return CliInvoker().invoke(app, list(args))


def _profile(path: Path, name: str = "default") -> dict[str, object]:
    return read_config_dict(path)["profiles"][name]  # type: ignore[no-any-return]


def test_get_an_old_key_reads_the_new_one_with_a_warning(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    renamed:\n      corpus_path: /c\n")

    result = _config("get", "renamed.corpus_path")

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "/c"
    assert (
        "warning: renamed.corpus_path is deprecated and will be removed in the next major "
        "release; use renamed.cache_dir"
    ) in result.stderr


def test_get_an_old_key_the_file_also_has_warns_once_with_the_hint(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    renamed:\n      corpus_path: /c\n")

    result = _config("get", "renamed.corpus_path")

    assert result.exit_code == 0, result.output
    assert result.stderr.count("renamed.corpus_path is deprecated") == 1
    assert "hint: run `untaped config migrate` to rename it in config.yml" in result.stderr


def test_get_a_retired_key_is_an_unknown_setting(_isolated_config: Path) -> None:
    result = _config("get", "renamed.ancient_path")

    assert result.exit_code != 0
    assert "unknown setting: 'renamed.ancient_path' (retired; now renamed.cache_dir)" in (
        result.stderr
    )
    assert "hint: run `untaped config migrate`" in result.stderr


def test_set_the_new_key_removes_the_old_spelling(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    renamed:\n      corpus_path: /c\n")

    result = _config("set", "renamed.cache_dir", "/n")

    assert result.exit_code == 0, result.output
    assert "set renamed.cache_dir in profile default; removed renamed.corpus_path" in result.stderr
    assert _profile(_isolated_config) == {"renamed": {"cache_dir": "/n"}}


def test_set_an_old_key_writes_the_new_one(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    renamed:\n      sweep:\n        sync_concurrency: 3\n",
    )

    result = _config("set", "renamed.sweep.sync_concurrency", "4", "--format", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["key"] == "renamed.sweep.parallel"
    assert _profile(_isolated_config) == {"renamed": {"sweep": {"parallel": 4}}}


@pytest.mark.parametrize("key", ["renamed.corpus_path", "renamed.cache_dir"])
def test_unset_removes_every_spelling(_isolated_config: Path, key: str) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    renamed:\n      corpus_path: /c\n      ancient_path: /a\n",
    )

    result = _config("unset", key)

    assert result.exit_code == 0, result.output
    assert (
        "unset renamed.cache_dir in profile default; removed renamed.ancient_path, "
        "renamed.corpus_path"
    ) in result.stderr
    assert _profile(_isolated_config) == {}


def test_set_dry_run_writes_nothing(_isolated_config: Path) -> None:
    text = "profiles:\n  default:\n    renamed:\n      corpus_path: /c\n"
    write_config(_isolated_config, text)

    result = _config("set", "renamed.cache_dir", "/n", "--dry-run", "--format", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["action"] == "planned"
    assert _isolated_config.read_text() == text


def _list_rows(*args: str) -> dict[str, dict[str, object]]:
    result = _config("list", "--format", "json", *args)
    assert result.exit_code == 0, result.output
    return {row["key"]: row for row in json.loads(result.stdout)}


def test_list_notes_a_value_from_an_old_key(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    renamed:\n      corpus_path: /c\n")

    row = _list_rows()["renamed.cache_dir"]

    assert (row["value"], row["source"], row["note"]) == (
        "/c",
        "profile:default",
        "from deprecated renamed.corpus_path",
    )


def test_list_notes_only_the_layer_that_won(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "active: prod\nprofiles:\n  default:\n    renamed:\n      corpus_path: /c\n"
        "  prod:\n    renamed:\n      cache_dir: /p\n",
    )

    row = _list_rows()["renamed.cache_dir"]

    assert (row["value"], row["note"]) == ("/p", None)


def test_list_notes_an_old_environment_variable(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_RENAMED__CORPUS_PATH", "/e")

    row = _list_rows()["renamed.cache_dir"]

    assert (row["value"], row["source"], row["note"]) == (
        "/e",
        "env",
        "from deprecated UNTAPED_RENAMED__CORPUS_PATH",
    )


def test_list_shows_a_value_from_an_old_key_in_the_deprecated_table(
    _isolated_config: Path,
) -> None:
    before = _config("list").stdout
    assert "Deprecated" not in before
    assert " note " not in before.splitlines()[1]

    write_config(_isolated_config, "profiles:\n  default:\n    renamed:\n      corpus_path: /c\n")

    before_heading, _, after_heading = _config("list").stdout.partition("Deprecated\n")
    assert " note " not in before_heading.splitlines()[1]
    assert "renamed.cache_dir" not in before_heading
    assert "renamed.cache_dir" in after_heading
    assert "from deprecated renamed.corpus_path" in after_heading


def test_list_all_profiles_shows_new_names_with_the_note(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    renamed:\n      cache_dir: /d\n"
        "  work:\n    renamed:\n      sweep:\n        sync_concurrency: 2\n",
    )

    result = _config("list", "--all-profiles", "--format", "json")

    assert result.exit_code == 0, result.output
    rows = [(row["profile"], row["key"], row["note"]) for row in json.loads(result.stdout)]
    assert rows == [
        ("default", "renamed.cache_dir", None),
        ("work", "renamed.sweep.parallel", "from deprecated renamed.sweep.sync_concurrency"),
    ]
