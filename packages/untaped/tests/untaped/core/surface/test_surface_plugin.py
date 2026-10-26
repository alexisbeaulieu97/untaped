"""``plugin_lines``: one plugin's settings, state and contract lines, composed alone."""

from __future__ import annotations

import warnings
from abc import abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, ClassVar, Literal

import pytest
from cyclopts import App
from pydantic import BaseModel, Field

from untaped.core.settings import CoreSettings
from untaped.core.surface import DeprecatedMark, Line, SurfaceError, generate, plugin_lines
from untaped.core.surface._sdk import own_mark
from untaped.core.surface._settings import settings_lines
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginSpec
from untaped.sdk.contracts import Contract
from untaped.sdk.health.deprecated_keys import key_mappings
from untaped.sdk.marks import experimental
from untaped.sdk.testing import plugin_candidate

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
    assert {line.mark for line in lines} == {"experimental", DeprecatedMark()}


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


def test_the_whole_surface_is_cores_lines_and_each_plugins() -> None:
    bins = plugin_candidate(
        PluginSpec(name="bins", settings=Settings, contracts=lambda: (BinSource,))
    )

    core = set(generate([]))

    assert set(generate([bins])) == core | set(plugin_lines(bins))
    assert "cli untaped config list" in {line.text for line in core}
    assert "cli untaped --help switch" in {line.text for line in core}


def test_a_root_command_no_plugin_owns_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    build = bootstrap.build_root_app

    def with_a_stray(**kwargs: object) -> App:
        root = build(**kwargs)  # type: ignore[arg-type]
        root.command(App(name="stray"))
        return root

    monkeypatch.setattr(bootstrap, "build_root_app", with_a_stray)

    with pytest.raises(SurfaceError, match="no composed plugin owns: stray"):
        generate([])


def test_lines_do_not_depend_on_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = PluginSpec(name="bins", settings=Settings, contracts=lambda: (BinSource,))
    before = plugin_lines(spec)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert plugin_lines(spec) == before
    assert Line("cfg bins.timeout number? default=unset") in before


@pytest.fixture
def renamed_skills_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """``untaped.skills.updates`` as if it had been renamed from ``untaped.skills.auto_update``."""
    renamed = {"skills.auto_update": "skills.updates"}
    monkeypatch.setattr(CoreSettings, "renamed_keys", renamed, raising=False)
    key_mappings.cache_clear()
    yield
    key_mappings.cache_clear()


@pytest.mark.usefixtures("renamed_skills_key")
def test_core_s_section_keeps_its_renamed_key() -> None:
    lines = settings_lines(bootstrap.compose_root(candidates=[]))
    renamed = Line("cfg untaped.skills.auto_update -> untaped.skills.updates", DeprecatedMark())
    assert renamed in lines


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


@experimental
class Crate(BaseModel):
    """A payload model still experimental itself."""

    label: str


class Wrapper(BaseModel):
    crate: Crate


def test_a_stable_contract_reaching_an_experimental_model_is_refused() -> None:
    """S2: stabilising a contract never promises an experimental model by accident."""

    class CrateSource(Contract):
        @abstractmethod
        def crates(self) -> list[Wrapper]: ...

    with pytest.raises(SurfaceError, match=r"the stable crate_source\.crates reaches Crate"):
        plugin_lines(PluginSpec(name="bins", contracts=lambda: (CrateSource,)))

    @experimental
    class CrateDraft(Contract):
        @abstractmethod
        def crates(self) -> list[Wrapper]: ...

    lines = plugin_lines(PluginSpec(name="bins", contracts=lambda: (CrateDraft,)))
    assert Line("contract bins.Crate(label)", "experimental") in lines


class StableCrate(Crate):
    """Unmarked: a base class's mark is not the model's own."""


def test_a_model_does_not_inherit_its_base_class_mark() -> None:
    class StableCrateSource(Contract):
        @abstractmethod
        def crates(self) -> list[StableCrate]: ...

    lines = plugin_lines(PluginSpec(name="bins", contracts=lambda: (StableCrateSource,)))
    assert any(line.text.startswith("contract bins.StableCrate(") for line in lines)


def test_an_exported_subclass_does_not_inherit_its_base_class_mark() -> None:
    assert own_mark(StableCrate) is None
    assert own_mark(Crate) == "experimental"


def test_a_subclass_does_not_inherit_warnings_deprecated() -> None:
    @warnings.deprecated("old")
    class Old:
        pass

    with pytest.warns(DeprecationWarning):

        class New(Old):
            pass

    assert own_mark(Old) == DeprecatedMark()
    assert own_mark(New) is None
