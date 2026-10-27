"""A plugin class's state rows on the surface and in the config reference's sections."""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel

from untaped.core.surface._settings import sections, settings_lines
from untaped.runtime.registry import CompositionResult, PluginCandidate, compose
from untaped.sdk import Key, Plugin, StateRows

pytestmark = pytest.mark.usefixtures("home_run")


class Thing(BaseModel):
    """A thing the plugin keeps.

    More about it.
    """

    name: Annotated[str, Key]
    size: int = 0


class Settings(BaseModel):
    pass


class Acme(Plugin):
    """Acme."""

    settings = Settings
    things = StateRows[Thing]()
    checkouts = StateRows[Thing].per_project()


def _composed() -> CompositionResult:
    return compose([PluginCandidate(distribution="untaped-acme", name="acme", target=Acme)])


def test_a_plugin_classs_rows_have_state_lines() -> None:
    texts = [line.text for line in settings_lines(_composed(), owner="acme")]
    assert "state acme.things.name string" in texts
    assert "state acme.things.size integer default=0" in texts
    assert "state acme.checkouts.name string" in texts


def test_a_plugin_classs_state_section_names_its_file_and_home_rows() -> None:
    [section] = [s for s in sections(_composed(), extensions=False, owner="acme") if s.is_state]
    assert section.file == "~/.untaped/plugins/acme/state.yml"
    assert list(section.model.model_fields) == ["things"]
    assert section.model.model_fields["things"].description == "A thing the plugin keeps."
