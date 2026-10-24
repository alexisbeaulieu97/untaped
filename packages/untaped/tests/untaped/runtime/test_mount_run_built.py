"""``run_built`` runs a built command; ``watching`` records what each command printed."""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import redirect_stdout
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.composition import PluginEntry
from untaped.runtime.mount import Mount, compose_plugins, run_built, watching
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import Command, Confirm, Key, Output, Plugin, Positional, Record
from untaped.sdk.errors import UsageError
from untaped.sdk.testing import CliInvoker


class Crate(Record, kind="depot.crate"):
    """One crate."""

    name: Annotated[str, Key]


class Listing(BaseModel):
    """How crates are listed."""

    output: Output[Crate]


class Strict(BaseModel):
    """A bundle that needs more than its core field."""

    output: Output[Crate]
    floor: int = Field(description="The lowest crate.")


class Both(BaseModel):
    """A bundle of two core fields."""

    output: Output[Crate]
    confirm: Confirm


class Crates(Command):
    """List the crates named."""

    names: Positional[list[str]] = Field(default_factory=list, description="The crates.")
    output: Output[Crate]

    def run(self) -> Iterator[Crate]:
        yield from (Crate(name=name) for name in self.names)


class Shelved(Command):
    """List one crate, laid out by an optional bundle."""

    listing: Listing | None = None

    def run(self) -> Iterator[Crate]:
        yield Crate(name="shelved")


class Picky(Command):
    """List one crate, through a bundle needing more."""

    strict: Strict | None = None

    def run(self) -> Iterator[Crate]:
        yield Crate(name="picky")


class Paired(Command):
    """List one crate, through a bundle of two core fields."""

    both: Both | None = None

    def run(self) -> Iterator[Crate]:
        yield Crate(name="paired")


class Label(Command):
    """Print one label."""

    def run(self) -> str:
        return "fragile"


class DepotSettings(BaseModel):
    """Nothing to configure."""


class Depot(Plugin):
    """Keep crates (a test's plugin)."""

    settings = DepotSettings
    commands = (Crates, Shelved, Picky, Paired, Label)


@pytest.fixture
def mount() -> Mount:
    return compose_plugins([PluginEntry("depot", f"{__name__}:Depot", Depot)])


def _decl(mount: Mount, name: str):  # type: ignore[no-untyped-def]
    return next(d for d in mount.declaration.commands() if d.path == ("depot", name))


def test_run_built_fills_core_fields_from_flags_and_prints(mount: Mount) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
        printed = run_built(
            mount,
            _decl(mount, "crates"),
            Crates(names=["a", "b"]),
            {"output": {"--format": "json"}},
        )
    assert (printed.format, printed.rows) == ("json", [Crate(name="a"), Crate(name="b")])
    assert out.getvalue() == '[{"name": "a"}, {"name": "b"}]\n'


def test_run_built_builds_an_optional_bundle_around_its_core_field(mount: Mount) -> None:
    with redirect_stdout(io.StringIO()):
        printed = run_built(
            mount, _decl(mount, "shelved"), Shelved(), {"listing.output": {"--format": "pipe"}}
        )
    assert (printed.format, printed.rows) == ("pipe", [Crate(name="shelved")])
    with pytest.raises(UsageError, match="floor"):
        run_built(mount, _decl(mount, "picky"), Picky(), {"strict.output": {}})


def test_run_built_builds_a_bundle_once_from_all_its_core_fields(mount: Mount) -> None:
    with redirect_stdout(io.StringIO()):
        printed = run_built(
            mount,
            _decl(mount, "paired"),
            Paired(),
            {"both.output": {"--format": "json"}, "both.confirm": {}},
        )
    assert (printed.format, printed.rows) == ("json", [Crate(name="paired")])


def test_run_built_keeps_a_line(mount: Mount) -> None:
    with redirect_stdout(io.StringIO()):
        printed = run_built(mount, _decl(mount, "label"), Label(), {})
    assert (printed.format, printed.text, printed.rows) == (None, "fragile", [])


def test_watching_records_what_each_dispatched_command_printed() -> None:
    root = build_root_app(
        candidates=[PluginCandidate(distribution="untaped-depot", name="depot", target=Depot)]
    )
    with watching() as printed:
        result = CliInvoker().invoke(root.meta, ["depot", "crates", "x", "--format", "json"])
    assert result.exit_code == 0
    [one] = printed
    assert (one.decl.path, one.format, one.rows) == (("depot", "crates"), "json", [Crate(name="x")])
