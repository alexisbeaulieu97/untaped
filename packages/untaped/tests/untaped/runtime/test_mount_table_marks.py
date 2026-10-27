"""``Bytes``, ``Age`` and ``Spread``: a table's cells in words, other formats raw."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Annotated, ClassVar

from pydantic import BaseModel

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


class Copies(Command[Nothing]):
    """List the stored copies."""

    output: Output[Stored]

    def run(self) -> Iterator[Stored]:
        yield Stored(
            name="api",
            size=1536,
            fetched=now() - timedelta(hours=3),
            hits={"grep:a": 2, "has-file:x": 1},
        )


class Keeper(Plugin):
    """Keep copies (a test's plugin)."""

    settings = Nothing
    commands = (Copies,)


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


def test_an_unknown_column_is_still_refused(install: Installer, clock: ManualClock) -> None:
    del clock
    ran = install(Keeper).cli("keeper copies -c nope")
    assert ran.exit == 2
    assert "nope" in "".join(ran.stderr)


def test_json_keeps_the_raw_values(install: Installer, clock: ManualClock) -> None:
    [row] = json.loads(install(Keeper).cli("keeper copies -f json").stdout)
    assert row["size"] == 1536
    assert row["hits"] == {"grep:a": 2, "has-file:x": 1}
    assert datetime.fromisoformat(row["fetched"]) == clock.now() - timedelta(hours=3)
