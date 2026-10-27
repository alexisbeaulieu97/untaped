"""The target a piped row names, and one directory named twice."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from untaped_recipe.application.targets import Target, dedupe_targets, target_of


class _Row(BaseModel):
    """A piped row of any kind: only its fields matter."""

    model_config = ConfigDict(extra="allow")


def _row(**fields: object) -> _Row:
    return _Row(**fields)


def test_an_absolute_target_path_wins_over_the_path() -> None:
    record = {"path": "/tmp/ws", "target_path": "/tmp/ws/api", "repo": "api"}

    assert target_of(_row(**record)) == Target(path=Path("/tmp/ws/api"), record=record)


def test_a_row_without_a_target_path_names_its_path() -> None:
    record = {"path": "/tmp/checkout", "repo": "api"}

    assert target_of(_row(**record)) == Target(path=Path("/tmp/checkout"), record=record)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"path": "/tmp/ws", "target_path": "api"}, "target_path must be absolute"),
        ({"path": "/tmp/ws", "target_path": "   "}, "target_path must be a non-empty string"),
        ({"name": "api"}, "names no target"),
        ({"path": "  "}, "names no target"),
    ],
)
def test_a_row_naming_no_usable_target_is_refused(record: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        target_of(_row(**record))


def test_one_directory_named_twice_is_planned_once(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    first = Target(path=tmp_path / "a")

    assert dedupe_targets([first, Target(path=tmp_path / "a" / "."), Target(path=tmp_path)]) == [
        first,
        Target(path=tmp_path),
    ]
