"""``untaped hello greet``."""

from __future__ import annotations

from collections.abc import Callable

from untaped_hello.commands.greet import DefaultGreeting, Greet
from untaped_hello.plugin import Hello
from untaped_hello.settings import HelloSettings

from untaped.sdk.testing import Install

Installer = Callable[..., Install]


def test_hello_greets_with_its_setting(install: Installer) -> None:
    ran = install(Hello).run(Greet())
    assert (ran.exit, ran.text) == (0, "hello from untaped-hello")


def test_hello_greets_with_a_configured_greeting(install: Installer) -> None:
    ran = install(Hello, settings=HelloSettings(greeting="hi")).run(Greet())
    assert (ran.exit, ran.text, ran.diagnostics) == (0, "hi", ())


def test_hello_names_the_setting_while_the_greeting_is_the_default(install: Installer) -> None:
    ran = install(Hello).run(Greet())
    assert [type(each) for each in ran.diagnostics] == [DefaultGreeting]
    assert ran.stderr[0] == "hint: this is the default greeting"
