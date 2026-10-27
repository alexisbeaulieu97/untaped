"""``Bytes``, ``Age`` and ``Spread``: a table's cells in words, other formats raw."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Annotated, ClassVar, Self

from pydantic import BaseModel, Field, model_validator

from untaped.sdk import Age, Bytes, Command, Output, Plugin, Record, Spread, now
from untaped.sdk.testing import Install, ManualClock

Installer = Callable[..., Install]


class Stored(Record):
    """One stored copy."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "size", "fetched", "hits")

    name: str
    size: Annotated[int, Bytes]
    fetched: Annotated[datetime, Age]
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
        self.output.columns[:] = ["name", "grep:a"]
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
