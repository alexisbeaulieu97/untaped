"""Composition refuses a server API block that can't work, naming the class or attribute.

Each case builds a plugin whose block is wrong in one way and checks the
problem composition reports for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Self

import pytest
from pydantic import BaseModel, Field

from core_test_support.server_api import V1, V2, ToyApi, ToyServer, ToySettings, ToyVersion, detect
from untaped.runtime.composition import PluginEntry, compose
from untaped.sdk import Plugin, deprecated, key
from untaped.sdk.health import NeedsSetup
from untaped.sdk.network import Credential, CredentialKey, Release, ServerApi, ServerVersion

TOKEN = Credential(key=ToyServer)


def _problems(block: object, settings: type[BaseModel] = ToySettings) -> list[str]:
    holder = type(
        "Holder",
        (Plugin,),
        {"__doc__": "Holds a block.", "settings": settings, "token": TOKEN, "server_api": block},
    )
    declaration = compose([PluginEntry("holder", f"{__name__}:Holder", holder)])
    return [
        problem.detail
        for quarantined in declaration.quarantined
        for problem in quarantined.problems
        if problem.rule == "block-shape"
    ]


def _block(*, adapters: tuple[Any, ...] = (V2, V1), **changed: Any) -> ServerApi[Any, Any, Any]:
    fields: dict[str, Any] = {
        "server": key(ToySettings).server,
        "credential": TOKEN,
        "detect": detect,
        "adapters": adapters,
        "override": key(ToySettings).server_version,
    }
    return ServerApi[ToyApi, ToySettings, ToyVersion](**{**fields, **changed})


def test_a_sound_block_has_no_problem() -> None:
    assert _problems(_block()) == []


def test_an_unsubscripted_block_is_refused() -> None:
    unsubscripted = ServerApi(
        server=key(ToySettings).server,
        credential=TOKEN,
        detect=detect,
        adapters=(V2,),
        override=key(ToySettings).server_version,
    )
    assert _problems(unsubscripted) == [
        "a ServerApi names its port, settings and version types: declare it subscripted, "
        "as in ServerApi[JiraApi, JiraSettings, JiraVersion](...)"
    ]


def test_no_adapter_is_refused() -> None:
    assert _problems(_block(adapters=())) == [
        "it has no adapters: name at least one ApiAdapter subclass"
    ]


class Unrecorded(V2):
    """Toy 3 and later, unrecorded."""

    recorded = ()


class Unserved(V2):
    """Toy 3 and later, recording a version it doesn't serve."""

    recorded = (ToyVersion.parse("Toy 1.4"),)


class Undocumented(V2):
    recorded = (ToyVersion.parse("Toy 3.0"),)


class Overlapping(V1):
    """Toy 1 and later."""

    @classmethod
    def serves(cls, version: ToyVersion) -> bool:
        return version.release >= (1,)


@pytest.mark.parametrize(
    ("adapters", "problem"),
    [
        ((Unrecorded,), "its adapter Unrecorded records no version"),
        ((Unserved,), "its adapter Unserved does not serve 'Toy 1.4', which it records"),
        ((Undocumented,), "its adapter Undocumented has no docstring"),
        ((V2, Overlapping), "its adapters V2 and Overlapping both serve 'Toy 3.0'"),
    ],
)
def test_adapters_that_cant_be_picked_by_their_recorded_versions_are_refused(
    adapters: tuple[type, ...], problem: str
) -> None:
    assert any(each.startswith(problem) for each in _problems(_block(adapters=adapters)))


class Undated(V1):
    """Toy 1 to 2, undated."""

    stability = deprecated(replacement=V2)


class ReplacedByAStranger(V1):
    """Toy 1 to 2, replaced by an adapter the block doesn't have."""

    stability = deprecated(removed_in=13, replacement=Overlapping)


def test_a_deprecated_adapter_names_its_major_and_a_replacement_among_the_adapters() -> None:
    assert _problems(_block(adapters=(V2, Undated))) == [
        "its adapter Undated has a deprecated() mark without removed_in: name the major removing it"
    ]
    [problem] = _problems(_block(adapters=(V2, ReplacedByAStranger)))
    assert problem.startswith("its adapter ReplacedByAStranger's replacement")


@dataclass(frozen=True)
class Unreadable(ToyVersion):
    """A version whose parse doesn't read back its str."""

    @classmethod
    def parse(cls, text: str) -> Self:
        return cls(Release.parse("9.9"))


class ReadsBackWrong(V2):
    """Toy 3 and later, recording a version that doesn't read back."""

    recorded = (Unreadable(Release.parse("3.0")),)


def test_a_version_whose_parse_does_not_read_back_its_text_is_refused() -> None:
    block = ServerApi[ToyApi, ToySettings, Unreadable](
        server=key(ToySettings).server,
        credential=TOKEN,
        detect=detect,
        adapters=(ReadsBackWrong,),
        override=key(ToySettings).server_version,
    )
    problems = _problems(block)
    assert any("Unreadable.parse(str(v)) != v" in each for each in problems)


@dataclass(frozen=True)
class Unparsed(ServerVersion):
    """A version that says its text but can't read one."""

    release: Release

    def __str__(self) -> str:
        return f"Toy {self.release}"


def test_a_version_type_without_parse_is_refused() -> None:
    block = ServerApi[ToyApi, ToySettings, Unparsed](
        server=key(ToySettings).server,
        credential=TOKEN,
        detect=detect,
        adapters=(V2,),
        override=key(ToySettings).server_version,
    )
    assert "its version type Unparsed must define both __str__() and parse()" in _problems(block)


@dataclass
class Unhashable(ServerVersion):
    """Not frozen: equal by value, so not hashable."""

    release: Release

    def __str__(self) -> str:
        return f"Toy {self.release}"

    @classmethod
    def parse(cls, text: str) -> Self:
        return cls(Release.parse(text.removeprefix("Toy ")))


def test_a_version_type_that_is_not_hashable_is_refused() -> None:
    block = ServerApi[ToyApi, ToySettings, Unhashable](
        server=key(ToySettings).server,
        credential=TOKEN,
        detect=detect,
        adapters=(V2,),
        override=key(ToySettings).server_version,
    )
    assert any("is not hashable" in each for each in _problems(block))


class OtherServer(CredentialKey):
    url: str = "https://other.example.com"


class OtherSettings(BaseModel):
    server: Annotated[ToyServer | None, NeedsSetup] = Field(default=None, description="Server.")
    url: str = Field(default="", description="Not a key.")
    server_version: ToyVersion | None = Field(default=None, description="Override.")
    version_text: str | None = Field(default=None, description="Not a version.")


def test_keys_must_be_the_plugins_own_and_of_the_right_types() -> None:
    assert any(
        "server= is key(OtherSettings).server, not a setting of the plugin's own" in each
        for each in _problems(_block(server=key(OtherSettings).server))
    )
    other = Credential(key=OtherServer)
    assert "its credential= is for OtherServer" in " ".join(_problems(_block(credential=other)))
    block = ServerApi[ToyApi, OtherSettings, ToyVersion](
        server=key(OtherSettings).url,
        credential=TOKEN,
        detect=detect,
        adapters=(V2,),
        override=key(OtherSettings).version_text,
    )
    problems = " ".join(_problems(block, settings=OtherSettings))
    assert "does not hold a CredentialKey" in problems
    assert "is not a field typed ToyVersion | None" in problems


def test_the_port_must_be_a_protocol() -> None:
    block = ServerApi[V2, ToySettings, ToyVersion](  # type: ignore[type-var]
        server=key(ToySettings).server,
        credential=TOKEN,
        detect=detect,
        adapters=(V2,),
        override=key(ToySettings).server_version,
    )
    assert any("is not a Protocol" in each for each in _problems(block))


def test_construction_refuses_what_is_not_a_key_a_credential_or_a_function() -> None:
    with pytest.raises(TypeError, match="take key"):
        _block(server="server")
    with pytest.raises(TypeError, match="takes a Credential"):
        _block(credential="token")
    with pytest.raises(TypeError, match="takes a function"):
        _block(detect=None)


@pytest.mark.parametrize("cache_first", [False, True])
def test_holding_a_part_again_is_a_duplicate_block(cache_first: bool) -> None:
    block = _block()
    held = {"server_api": block, "detection": block.cache}
    if cache_first:
        held = {"detection": block.cache, "server_api": block}
    holder = type(
        "Holder",
        (Plugin,),
        {"__doc__": "Holds a block.", "settings": ToySettings, "token": TOKEN, **held},
    )
    declaration = compose([PluginEntry("holder", f"{__name__}:Holder", holder)])
    rules = [
        (problem.rule, problem.detail)
        for quarantined in declaration.quarantined
        for problem in quarantined.problems
    ]
    assert any(
        rule == "duplicate-block" and "is the same cache as" in detail for rule, detail in rules
    ), rules
