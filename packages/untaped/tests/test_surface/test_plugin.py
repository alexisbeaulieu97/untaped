"""``plugin_lines``: one plugin's settings, state and contract lines, composed alone."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, ClassVar, Literal

import pytest
from pydantic import BaseModel, Field

from untaped import bootstrap
from untaped.contracts import Contract
from untaped.deprecated_keys import key_mappings
from untaped.plugins.registry import PluginSpec
from untaped.settings import SkillsSettings
from untaped.stability import experimental
from untaped.surface import Line, plugin_lines
from untaped.surface._settings import settings_lines

pytestmark = pytest.mark.usefixtures("fresh_composition")


class Window(BaseModel):
    days: Annotated[int, Field(ge=1)] = 7


class Settings(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"timeout_secs": "timeout"}

    timeout: float | None = None
    output: Literal["json", "table"] = "table"
    parallel: Annotated[int | None, experimental] = None
    window: Window = Field(default_factory=Window)
    root: Path = Field(default_factory=lambda: Path.home() / ".bins")


class State(BaseModel):
    seen: list[str] = Field(default_factory=list)


class Bin(BaseModel):
    name: Annotated[str, Field(min_length=1)]
    kind: Literal["glass", "paper"] = "paper"
    lid: Lid | None = None


class Lid(BaseModel):
    color: str


Bin.model_rebuild()


class BinSource(Contract):
    @abstractmethod
    def bins(self, *, full: bool = False) -> list[Bin]: ...


def _lines(spec: PluginSpec) -> set[str]:
    return {str(line) for line in plugin_lines(spec)}


def test_settings_and_state_lines() -> None:
    lines = _lines(PluginSpec(name="bins", settings=Settings, state=State))
    assert {line for line in lines if line.startswith(("cfg ", "state "))} == {
        "cfg bins.output choice=json",
        "cfg bins.output choice=table",
        "cfg bins.output string default=table",
        "cfg bins.parallel integer? default=unset  [experimental]",
        "cfg bins.root string(format=path) default=~/.bins",
        "cfg bins.timeout number? default=unset",
        "cfg bins.timeout_secs -> bins.timeout  [deprecated]",
        "cfg bins.window.days integer(minimum=1) default=7",
        "state bins.seen string[]",
    }


def test_an_experimental_plugin_marks_every_line() -> None:
    lines = plugin_lines(PluginSpec(name="bins", settings=Settings, stability=experimental))
    assert {line.mark for line in lines} == {"experimental", "deprecated"}


def test_a_contract_and_the_payload_models_it_reaches() -> None:
    lines = _lines(PluginSpec(name="bins", contracts=lambda: (BinSource,)))
    assert {line for line in lines if line.startswith("contract ")} == {
        "contract bins.Bin(name)",
        "contract bins.Bin.kind choice=glass",
        "contract bins.Bin.kind choice=paper",
        "contract bins.Bin.kind string default=paper",
        "contract bins.Bin.lid Lid? default=None",
        "contract bins.Bin.name string(minLength=1)",
        "contract bins.Lid(color)",
        "contract bins.Lid.color string",
        "contract bins.bin_source abstract=bins",
        "contract bins.bin_source.bins(*, full: boolean = false) -> Bin[]",
    }
    assert "cfg bins.extensions.bin_source.rank.bins string[] default=[]" in lines


def test_a_plugin_without_surface_has_no_lines() -> None:
    assert plugin_lines(PluginSpec(name="bins")) == []


def test_lines_do_not_depend_on_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = PluginSpec(name="bins", settings=Settings, contracts=lambda: (BinSource,))
    before = plugin_lines(spec)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert plugin_lines(spec) == before
    assert Line("cfg bins.timeout number? default=unset") in before


@pytest.fixture
def renamed_skills_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """``skills.updates`` as if it had been renamed from ``skills.auto_update``."""
    monkeypatch.setattr(SkillsSettings, "renamed_keys", {"auto_update": "updates"}, raising=False)
    key_mappings.cache_clear()
    yield
    key_mappings.cache_clear()


@pytest.mark.usefixtures("renamed_skills_key")
def test_a_root_section_keeps_its_renamed_key() -> None:
    lines = settings_lines(bootstrap.compose_root(candidates=[]))
    assert Line("cfg skills.auto_update -> skills.updates", "deprecated") in lines


def test_extension_rank_keys_take_their_methods_marks() -> None:
    @experimental
    class CrateSource(Contract):
        @abstractmethod
        def crates(self) -> list[Bin]: ...

    lines = plugin_lines(PluginSpec(name="bins", contracts=lambda: (CrateSource,)))
    assert (
        Line("cfg bins.extensions.crate_source.rank.crates string[] default=[]", "experimental")
        in lines
    )
