"""``Plugin``, ``Command``, ``Group``, ``derive`` and ``StubSkill``: what an author writes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, Field, ValidationError, model_validator

from untaped.sdk import (
    Column,
    Command,
    Confirm,
    Group,
    Key,
    Output,
    Plugin,
    Positional,
    Refresh,
    Stdin,
    StubSkill,
    derive,
    experimental,
)
from untaped.sdk.plugin import SpelledCommand, field_block, settings_model


class HelloSettings(BaseModel):
    greeting: str = Field("hello", description="What greet prints.")


class Salutation(BaseModel):
    name: Annotated[str, Key, Column]
    text: str


class Say(Command[HelloSettings]):
    """Greet each name."""

    stability = experimental
    names: Positional[list[str]] = Field(default_factory=list, description="Whom to greet.")
    stdin: Stdin[Salutation]
    output: Output[Salutation]
    confirm: Confirm

    def run(self) -> Iterator[Salutation]:
        yield from (Salutation(name=name, text=f"hi {name}") for name in self.names)


class Version(Command):
    """Print the version."""

    def run(self) -> str:
        return "1"


def test_a_command_builds_without_its_core_fields() -> None:
    say = Say(names=["Ada"])
    assert isinstance(say.stdin, Stdin)
    assert isinstance(say.output, Output)
    assert isinstance(say.confirm, Confirm)
    assert [row.name for row in say.run()] == ["Ada"]


def test_a_core_field_default_configures_it() -> None:
    marker = object()

    class Motd(Command):
        """Print the message of the day."""

        refresh: Refresh = Refresh(marker)

        def run(self) -> str:
            return ""

    assert Motd().refresh.caches == (marker,)


def test_the_json_schema_leaves_core_fields_out() -> None:
    schema = Say.model_json_schema()
    assert set(schema["properties"]) == {"names"}
    assert "required" not in schema


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Say(nope=1)  # type: ignore[call-arg]


def test_the_settings_model_is_the_type_parameter() -> None:
    assert settings_model(Say) is HelloSettings
    assert settings_model(Version) is None


def test_the_marks_are_class_variables() -> None:
    assert Say.stability is experimental
    assert "stability" not in Say.model_fields
    assert Version.stability is None


def test_a_command_without_run_says_so() -> None:
    class Empty(Command):
        """Nothing."""

    with pytest.raises(NotImplementedError, match="Empty has no run"):
        Empty().run()


def test_spelled_names_the_command_by_its_class() -> None:
    assert Say.spelled("Ada") == SpelledCommand(Say, ("Ada",))


def test_field_block_reads_a_parameterised_block() -> None:
    assert field_block(Output[Salutation]) is Output
    assert field_block(Confirm) is Confirm
    assert field_block(str) is None


def test_plugins_and_groups_are_never_instantiated() -> None:
    class Tools(Group):
        """Tools."""

    class Hello(Plugin):
        """Hello."""

        settings = HelloSettings

    with pytest.raises(TypeError, match="never an instance"):
        Tools()
    with pytest.raises(TypeError, match="never an instance"):
        Hello()


def test_a_stub_skill_needs_a_description() -> None:
    with pytest.raises(ValueError, match="empty"):
        StubSkill("  ")


def test_a_stub_skill_on_a_plugin_class_is_bound_to_it() -> None:
    class Hello(Plugin):
        """Hello."""

        settings = HelloSettings
        skill = StubSkill("Uses hello.")

    assert Hello.skill is not None
    assert Hello.skill.binding == (Hello, "skill")


class Scope(BaseModel):
    """Organisation scope."""

    organization: str | None = Field(None, description="The organisation.")


class InventoryScope(BaseModel):
    """Inventory scope."""

    inventory: str | None = Field(None, description="The inventory.")


class List(Command):
    """List resources."""

    kind: ClassVar[str] = "any"
    scope: Scope = Field(default_factory=Scope, description="Where to look.")
    with_scm: bool = Field(False, description="Show the SCM columns.")
    limit: int = Field(10, ge=1, description="How many.")

    @model_validator(mode="after")
    def _small(self) -> List:
        if self.limit > 100:
            raise ValueError("too many")
        return self

    def run(self) -> str:
        return f"{self.kind} {self.scope!r}"


def test_derive_replaces_a_field_type_and_sets_class_attributes() -> None:
    Hosts = derive(List, "Hosts", doc="List hosts.", kind="hosts", scope=InventoryScope)
    assert Hosts.__name__ == "Hosts"
    assert Hosts.__doc__ == "List hosts."
    assert Hosts.__module__ == List.__module__
    assert Hosts.kind == "hosts"  # type: ignore[attr-defined]
    hosts = Hosts(scope={"inventory": "web"})
    assert hosts.scope == InventoryScope(inventory="web")
    assert Hosts.model_fields["scope"].description == "Where to look."
    assert List.kind == "any"


def test_derive_drops_omitted_fields_and_keeps_validators() -> None:
    Plain = derive(List, "Plain", omit=("with_scm",))
    assert "with_scm" not in Plain.model_fields
    assert "with_scm" in List.model_fields
    with pytest.raises(ValidationError, match="extra"):
        Plain(with_scm=True)
    with pytest.raises(ValidationError, match="too many"):
        Plain(limit=101)


def test_derive_refuses_an_omit_naming_no_field() -> None:
    with pytest.raises(TypeError, match="omit names no field"):
        derive(List, "Bad", omit=("nope",))


def test_derive_makes_a_group_with_its_commands() -> None:
    Hosts = derive(Group, "Hosts", doc="Manage hosts.", commands=(List,))
    assert issubclass(Hosts, Group)
    assert Hosts.commands == (List,)
    assert Hosts.__doc__ == "Manage hosts."


def test_derive_takes_only_commands_and_groups() -> None:
    with pytest.raises(TypeError, match="Command or Group"):
        derive(int, "Nope")  # type: ignore[type-var]
