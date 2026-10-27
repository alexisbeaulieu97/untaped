"""``self.output.then(line)``: a closing stdout line after a table, alone under ``-q``."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

from pydantic import BaseModel, Field

from untaped.sdk import Command, OutcomeRecord, Output, Plugin, Positional
from untaped.sdk.testing import Install

Installer = Callable[..., Install]


class Made(OutcomeRecord):
    """One folder made."""

    name: str


class Nothing(BaseModel):
    """No settings."""


class Make(Command[Nothing]):
    """Make folders and print where the last one is."""

    names: Positional[list[str]] = Field(description="Folders to make.")
    fail: bool = Field(False, description="Fail the last one.")
    output: Output[Made]

    def run(self) -> Iterator[Made]:
        self.output.then(f"/made/{self.names[-1]}")
        for name in self.names:
            failed = self.fail and name == self.names[-1]
            yield Made(name=name, action="failed" if failed else "created")


class Maker(Plugin):
    """Make folders (a test's plugin)."""

    settings = Nothing
    commands = (Make,)


def test_the_line_follows_the_table(install: Installer) -> None:
    ran = install(Maker).cli("maker make a b")
    assert ran.exit == 0, ran.stderr
    lines = ran.stdout.splitlines()
    assert lines[-1] == "/made/b"
    assert any("created" in line for line in lines[:-1])


def test_quiet_with_nothing_failed_prints_the_line_alone(install: Installer) -> None:
    assert install(Maker).cli("-q maker make a b").stdout == "/made/b\n"


def test_quiet_with_a_failed_row_keeps_the_rows(install: Installer) -> None:
    ran = install(Maker).cli("-q maker make a b --fail")
    assert ran.exit == 1
    lines = ran.stdout.splitlines()
    assert lines[-1] == "/made/b"
    assert any("failed" in line for line in lines[:-1])


def test_other_formats_print_the_records_only(install: Installer) -> None:
    ran = install(Maker).cli("maker make a b --format json")
    assert [row["name"] for row in json.loads(ran.stdout)] == ["a", "b"]
