"""``Bytes``, ``Age``, ``Duration``, ``ObjectName``, ``Spread`` and ``TableText``: a table's
cells in words, other formats raw."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum
from typing import Annotated, Self

import pytest
from pydantic import BaseModel, Field, model_validator

from untaped.sdk import (
    Age,
    Bytes,
    CheckRecord,
    Command,
    Duration,
    ObjectName,
    OutcomeRecord,
    Output,
    Plugin,
    Record,
    Spread,
    Status,
    key,
    now,
)
from untaped.sdk.fields import status_roles, table_row, table_rows
from untaped.sdk.testing import Install, ManualClock

Installer = Callable[..., Install]


#: A full git object name.
FULL = "0123456789abcdef0123456789abcdef01234567"


class Stored(Record):
    """One stored copy."""

    name: str
    size: Annotated[int, Bytes]
    fetched: Annotated[datetime, Age]
    took: Annotated[float, Duration] = 102.5
    commit: Annotated[str, ObjectName] = FULL
    wait_s: float = 102.5  # no mark: shown as it is, whatever its name
    sha: str = FULL
    hits: Annotated[dict[str, int], Spread]


class Nothing(BaseModel):
    """No settings."""


#: The commands whose ``run`` started, so a test sees a refusal came first.
STARTED: list[str] = []


class Copies(Command[Nothing]):
    """List the stored copies."""

    output: Output[Stored]
    also: list[str] = Field(default_factory=list, description="Another key to count.")

    @model_validator(mode="after")
    def _keys(self) -> Self:
        self.output.columns.offer("grep:a", "has-file:x", *self.also)
        return self

    def run(self) -> Iterator[Stored]:
        STARTED.append("copies")
        yield Stored(
            name="api",
            size=1536,
            fetched=now() - timedelta(hours=3),
            hits={"grep:a": 2, "has-file:x": 1},
        )


class Narrow(Command[Nothing]):
    """List one copy, naming one of the keys its rows have."""

    output: Output[Stored]

    @model_validator(mode="after")
    def _keys(self) -> Self:
        self.output.columns.show(key(Stored).name, "grep:a")
        return self

    def run(self) -> Iterator[Stored]:
        yield Stored(name="api", size=1, fetched=now(), hits={"grep:a": 2, "zzz": 1})


class Keeper(Plugin):
    """Keep copies (a test's plugin)."""

    settings = Nothing
    commands = (Copies, Narrow)


def _header(text: str) -> str:
    return next(line for line in text.splitlines() if "name" in line)


def test_a_table_shows_sizes_and_ages_in_words(install: Installer, clock: ManualClock) -> None:
    del clock  # its time is the run's: "3 h ago" holds however long the test takes
    ran = install(Keeper).cli("keeper copies")
    assert ran.exit == 0, ran.stderr
    assert "1.5 KiB" in ran.stdout
    assert "3 h ago" in ran.stdout


def test_a_table_shows_marked_run_times_and_object_names_short(
    install: Installer, clock: ManualClock
) -> None:
    del clock
    ran = install(Keeper).cli("keeper copies -c name,took,commit,wait_s,sha")
    assert ran.exit == 0, ran.stderr
    assert "1m42s" in ran.stdout and "102.5" in ran.stdout  # took marked, wait_s not
    assert ran.stdout.count(FULL) == 1  # sha whole, commit cut
    assert f"{FULL[:10]} " in ran.stdout


def test_a_spread_field_is_a_column_per_key(install: Installer, clock: ManualClock) -> None:
    del clock
    header = _header(install(Keeper).cli("keeper copies").stdout)
    assert "grep:a" in header and "has-file:x" in header
    assert "hits" not in header


def test_columns_pick_one_key_of_a_spread_field(install: Installer, clock: ManualClock) -> None:
    del clock
    ran = install(Keeper).cli("keeper copies -c name,grep:a")
    assert ran.exit == 0, ran.stderr
    header = _header(ran.stdout)
    assert "grep:a" in header and "has-file:x" not in header


def test_an_unknown_column_is_refused_before_the_run(
    install: Installer, clock: ManualClock
) -> None:
    del clock
    STARTED.clear()
    ran = install(Keeper).cli("keeper copies -c nope")
    assert ran.exit == 2
    assert "unknown column 'nope'" in "".join(ran.stderr)
    assert STARTED == []


def test_a_key_the_command_names_but_no_row_has_is_an_empty_column(
    install: Installer, clock: ManualClock
) -> None:
    del clock
    ran = install(Keeper).cli("keeper copies --also grep:b -c name,grep:b")
    assert ran.exit == 0, ran.stderr
    assert "grep:b" in _header(ran.stdout)


def test_json_keeps_the_raw_values(install: Installer, clock: ManualClock) -> None:
    [row] = json.loads(install(Keeper).cli("keeper copies -f json").stdout)
    assert row["size"] == 1536
    assert (row["took"], row["commit"]) == (102.5, FULL)
    assert row["hits"] == {"grep:a": 2, "has-file:x": 1}
    assert datetime.fromisoformat(row["fetched"]) == clock.now() - timedelta(hours=3)


def test_a_built_command_run_by_the_kit_keeps_the_columns_it_names(
    install: Installer, clock: ManualClock
) -> None:
    del clock
    ran = install(Keeper).run(Narrow())
    assert ran.exit == 0, ran.stderr
    header = _header(ran.stdout)
    assert "grep:a" in header and "zzz" not in header


def test_columns_list_and_pick_only_the_keys_the_command_named(
    install: Installer, clock: ManualClock
) -> None:
    del clock
    keeper = install(Keeper)
    listed = keeper.cli("keeper narrow -c ?")
    refused = keeper.cli("keeper narrow -c +zzz")
    assert listed.exit == 0, listed.stderr
    shown = "\n".join(listed.stderr)
    assert "grep:a" in shown and "zzz" not in shown
    assert refused.exit == 2
    assert "unknown column 'zzz'" in "".join(refused.stderr)


class Edges(BaseModel):
    """Values a mark leaves as they are."""

    took: Annotated[float | bool | None, Duration] = None
    commit: Annotated[str, ObjectName] = ""


@pytest.mark.parametrize(
    ("record", "cells"),
    [
        (Edges(took=True), {"took": True}),
        (Edges(took=None), {"took": None}),
        (Edges(took=5), {"took": "5.0s"}),
        (Edges(commit="0123456789"), {"commit": "0123456789"}),
        (Edges(commit=FULL.upper()), {"commit": FULL.upper()}),
        (Edges(commit=FULL + FULL[:24]), {"commit": FULL + FULL[:24]}),
    ],
)
def test_a_mark_shapes_only_the_values_it_names(
    record: Edges, cells: dict[str, object], clock: ManualClock
) -> None:
    del clock
    row = record.model_dump(include=set(cells))
    [shown], _, _ = table_rows([record], [row], None)
    assert shown == cells


class Cloned(OutcomeRecord):
    """An outcome whose plugin adds its own verb to core's."""

    action: Annotated[str, Status(success=("cloned",), warning=("created",))]


class Plain(Record):
    """A column named ``status`` with no ``Status``."""

    status: str


class Phase(StrEnum):
    READY = "ready"
    BROKEN = "broken"


class Phased(Record):
    """An optional enum field whose ``Status`` names its members."""

    phase: Annotated[Phase, Status(success=(Phase.READY,), error=(Phase.BROKEN,))] | None = None


def _roles(record: BaseModel) -> dict[str, str | None]:
    [shown], _, _ = table_rows([record], [record.model_dump()], None)
    return {key: getattr(cell, "role", None) for key, cell in shown.items()}


@pytest.mark.parametrize(
    ("record", "roles"),
    [
        (OutcomeRecord(action="failed"), {"action": "error"}),
        (OutcomeRecord(action="unchanged"), {"action": None}),
        (Cloned(action="cloned"), {"action": "success"}),  # its own verb
        (Cloned(action="partial"), {"action": "error"}),  # core's, kept
        (Cloned(action="created"), {"action": "warning"}),  # the nearest class wins
        (Plain(status="failed"), {"status": None}),
        (Phased(phase=Phase.BROKEN), {"phase": "error"}),  # an enum, inside ``X | None``
        (Phased(), {"phase": None}),
    ],
)
def test_a_field_s_status_roles_merge_down_its_classes(
    record: BaseModel, roles: dict[str, str | None], clock: ManualClock
) -> None:
    del clock
    shown = _roles(record)
    assert {key: shown.get(key) for key in roles} == roles


def test_check_records_draw_core_s_check_words() -> None:
    assert status_roles(CheckRecord)["status"] == {
        "pass": "success",
        "warn": "warning",
        "fail": "error",
        "error": "error",
    }


def test_a_status_takes_words_only() -> None:
    class Level(IntEnum):
        LOW = 1

    with pytest.raises(TypeError, match="takes words"):
        Status(success=(Level.LOW,))
    with pytest.raises(TypeError, match="tuple of words"):
        Status(success="ok")  # type: ignore[arg-type]


def test_a_view_built_with_table_row_keeps_its_record_s_roles(clock: ManualClock) -> None:
    del clock
    record = OutcomeRecord(action="failed")
    shown = table_row(record, {"action": "failed", "note": "joined"})
    assert getattr(shown["action"], "role", None) == "error"
    assert shown["note"] == "joined"


class Spot(BaseModel):
    """Where a copy sits, read as one cell."""

    path: str
    line: int

    def table_text(self) -> str:
        return f"{self.path}:{self.line}"


class Plain(BaseModel):
    """A nested model with no text of its own."""

    path: str


class Placed(Record):
    """A copy and where it sits."""

    spot: Spot
    plain: Plain


def test_a_nested_model_with_table_text_reads_as_its_text_and_dumps_whole() -> None:
    placed = Placed(spot=Spot(path="src/a.py", line=3), plain=Plain(path="b"))
    row = table_row(placed)
    assert row["spot"] == "src/a.py:3"
    assert row["plain"] == {"path": "b"}  # flattened by the renderer, as before
    assert placed.model_dump(mode="json")["spot"] == {"path": "src/a.py", "line": 3}
