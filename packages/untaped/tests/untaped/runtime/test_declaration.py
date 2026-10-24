"""The declaration's lookups: plugins, owners, commands, settings sections and spelled hints."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.composition import PluginEntry, compose
from untaped.runtime.declaration import Declaration, Problem, target_of
from untaped.sdk import Command, Group, Plugin, key


class HelloSettings(BaseModel):
    greeting: str = Field("hello", description="What say prints.")


class Set(Command[HelloSettings]):
    """Set a setting."""

    def run(self) -> None:
        return None


class Get(Command):
    """Get a setting."""

    def run(self) -> str:
        return ""


class ConfigGroup(Group):
    """Read and write settings."""

    commands = (Set,)


class Root(Command):
    """The plugin's own command."""

    def run(self) -> None:
        return None


class Hello(Plugin):
    """Say hello."""

    settings = HelloSettings
    commands = (ConfigGroup, Get)
    runs = Root


@pytest.fixture(scope="module")
def declaration() -> Declaration:
    return compose([PluginEntry("hello", f"{__name__}:Hello", Hello)])


def test_plugin_looks_a_plugin_up_by_name(declaration: Declaration) -> None:
    assert declaration.plugin("hello").name == "hello"
    with pytest.raises(LookupError, match="no composed plugin named 'nope'"):
        declaration.plugin("nope")


def test_owner_of_is_the_plugin_whose_package_holds_the_module(declaration: Declaration) -> None:
    package = __name__.partition(".")[0]
    assert declaration.owner_of(f"{package}.anything") == "hello"
    assert declaration.owner_of("elsewhere.module") is None


def test_commands_walks_depth_first_with_the_root_first(declaration: Declaration) -> None:
    assert [c.path for c in declaration.commands()] == [
        ("hello",),
        ("hello", "config", "set"),
        ("hello", "get"),
    ]


def test_section_of_maps_a_settings_model_to_its_plugin(declaration: Declaration) -> None:
    assert declaration.section_of(target_of(HelloSettings)) == "hello"
    assert declaration.section_of("x:Other") is None


def test_spell_writes_the_command_line_a_hint_names(declaration: Declaration) -> None:
    spelled = Set.spelled(key(HelloSettings).greeting, "hi")
    assert declaration.spell(spelled) == "untaped hello config set hello.greeting hi"
    assert declaration.spell(Get.spelled()) == "untaped hello get"


def test_a_root_entry_s_commands_are_typed_without_its_name() -> None:
    root = compose([PluginEntry("hello", f"{__name__}:Hello", Hello, root=True)])
    assert root.plugin("hello").root
    assert root.plugin("hello").command_words() == {"config", "get"}
    assert root.spell(Set.spelled(key(HelloSettings).greeting, "hi")) == (
        "untaped config set hello.greeting hi"
    )
    [get] = [c for c in root.commands() if c.name == "get"]
    assert (get.path, root.words(get)) == (("hello", "get"), ("get",))


def test_spell_refuses_a_command_composition_never_saw(declaration: Declaration) -> None:
    class Stray(Command):
        """Stray."""

        def run(self) -> None:
            return None

    with pytest.raises(LookupError, match="is not a composed command"):
        declaration.spell(Stray.spelled())


def test_a_problem_reads_as_where_detail_and_rule() -> None:
    problem = Problem(rule="missing-run", where="Say", detail="defines no run()")
    assert str(problem) == "Say: defines no run() [missing-run]"


def test_target_of_names_code_by_module_and_qualname() -> None:
    assert target_of(Hello) == f"{__name__}:Hello"
