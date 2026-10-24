"""``untaped hello say``."""

from __future__ import annotations

import json
from collections.abc import Callable

from untaped_hello.commands.say import Say
from untaped_hello.plugin import Hello
from untaped_hello.records import Salutation
from untaped_hello.settings import HelloSettings

from untaped.sdk.testing import Install

Installer = Callable[..., Install]


def test_hello_say_greets_each_name(install: Installer) -> None:
    ran = install(Hello, settings=HelloSettings(greeting="hi")).run(Say(names=["ada", "grace"]))
    assert ran.rows_as(Salutation) == [
        Salutation(name="ada", text="hi, ada"),
        Salutation(name="grace", text="hi, grace"),
    ]


def test_hello_say_pipes_its_greetings_back_in_by_their_model(install: Installer) -> None:
    hello = install(Hello)
    piped = hello.cli("hello say ada grace --format pipe")
    assert piped.exit == 0, piped.stderr
    assert {json.loads(line)["kind"] for line in piped.stdout.splitlines()} == {"hello.salutation"}

    again = hello.cli("hello say --stdin --format json", input=piped.stdout)
    assert [row.name for row in again.rows_as(Salutation)] == ["ada", "grace"]
    assert hello.run(Say(), stdin=again.rows).rows_as(Salutation) == again.rows


def test_hello_say_refuses_records_of_another_kind(install: Installer) -> None:
    hello = install(Hello)
    plugins = hello.cli("plugin list --format pipe").stdout

    ran = hello.cli("hello say --stdin", input=plugins)

    assert ran.exit == 2
    assert any("expected 'hello.salutation'" in line for line in ran.stderr)
