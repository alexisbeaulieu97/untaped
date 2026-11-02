"""A name a plugin reaches that untaped or another plugin doesn't have: an error saying so.

The plugins are real installs: ``untaped-shelf`` exports ``Bulb`` (defined in
a private module) and ``Shade`` (a model that forbids extra fields) from its
``api``; ``untaped-acme``'s ``poke`` command runs what each test writes, out
of the startup check's sight (a name on a returned object, a lookup by
``getattr`` or ``import_module``).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core_test_support.runtime.plugin_uses_site import Site, installed_site
from untaped.runtime import bootstrap, plugin_frames, run_output
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.testing import CliInvoker

_SHELF = {
    "untaped_shelf/__init__.py": "",
    "untaped_shelf/plugin.py": '''
        """The ``shelf`` plugin class."""

        from pydantic import BaseModel, ConfigDict

        from untaped.sdk import Plugin


        class ShelfSettings(BaseModel):
            model_config = ConfigDict(frozen=True)


        class Shelf(Plugin):
            """Bulbs."""

            settings = ShelfSettings
        ''',
    "untaped_shelf/_bulbs.py": '''
        """Where shelf's API is defined; plugins import it from ``api``."""

        from pydantic import BaseModel, ConfigDict


        class Bulb:
            def bright(self) -> str:
                return "bright"


        class Shade(BaseModel):
            model_config = ConfigDict(extra="forbid")

            tint: str = "white"


        class Frame(BaseModel):
            model_config = ConfigDict(extra="forbid")

            shade: Shade = Shade()


        class Knob:
            def __getattr__(self, name: str) -> object:
                raise AttributeError(f"a knob has no {name}")


        def bulb() -> Bulb:
            return Bulb()
        ''',
    "untaped_shelf/fittings/__init__.py": "",
    "untaped_shelf/fittings/kinds.py": "SCREW = 'E27'\n",
    "untaped_shelf/api.py": '''
        """What a plugin may import from shelf."""

        from untaped_shelf._bulbs import Bulb, Frame, Knob, Shade, bulb

        __all__ = ["Bulb", "Frame", "Knob", "Shade", "bulb", "ghost"]
        ''',
}

_ACME_PLUGIN = '''
    """The ``acme`` plugin class: ``poke`` runs ``untaped_acme.poking.poke``."""

    from pydantic import BaseModel, ConfigDict

    from untaped.sdk import Command, Plugin


    class Poke(Command):
        """Poke."""

        def run(self) -> None:
            from untaped_acme import poking

            poking.poke()


    class AcmeSettings(BaseModel):
        model_config = ConfigDict(frozen=True)


    class Acme(Plugin):
        """Pokes."""

        settings = AcmeSettings
        commands = (Poke,)
    '''


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(warnings, "_showwarnmsg_impl", run_output._SHOW_UNRECORDED)
    plugin_frames.forget()
    with installed_site(tmp_path, monkeypatch) as installed:
        yield installed
    plugin_frames.forget()


def _install(site: Site, poke: str, *, shelf: str = "4.0", floor: str = "3.0") -> list[Any]:
    shelf_candidate = site.install("untaped-shelf", _SHELF, at=shelf)
    acme = site.install(
        "untaped-acme",
        {
            "untaped_acme/__init__.py": "",
            "untaped_acme/plugin.py": _ACME_PLUGIN,
            "untaped_acme/poking.py": poke,
            "untaped_acme/own.py": "class Gadget:\n    pass\n",
        },
        requires=(f"untaped-shelf>={floor}",),
    )
    return [acme, shelf_candidate]


def _invoke(found: list[PluginCandidate], *argv: str) -> Any:
    root = bootstrap.build_root_app(candidates=found).meta
    return CliInvoker().invoke(root, list(argv))


_OUT_OF_DATE = (
    "untaped-shelf 4.0 doesn't have: untaped-acme is out of date (built for untaped-shelf 3.0)"
)


def test_a_member_gone_from_a_returned_object_names_it_by_its_public_module(site: Site) -> None:
    found = _install(site, "from untaped_shelf.api import bulb\n\ndef poke():\n    bulb().dim()\n")
    result = _invoke(found, "acme", "poke")
    assert result.exit_code == 4, result.output
    assert result.stderr.splitlines() == [
        f"error: acme can't run this: it uses untaped_shelf.api.Bulb.dim, which {_OUT_OF_DATE}",
        "hint: upgrade untaped-acme, or remove it (--verbose shows where)",
    ]


def test_verbose_shows_the_traceback(site: Site) -> None:
    found = _install(site, "from untaped_shelf.api import bulb\n\ndef poke():\n    bulb().dim()\n")
    result = _invoke(found, "--verbose", "acme", "poke")
    assert result.exit_code == 4
    assert "Traceback (most recent call last)" in result.stderr
    assert "untaped_acme/poking.py" in result.stderr
    assert "error: acme can't run this" in result.stderr


@pytest.mark.parametrize(
    ("poke", "spelled"),
    [
        (
            "from untaped_shelf import api\n\ndef poke():\n    getattr(api, 'lamp')\n",
            "untaped_shelf.api.lamp",
        ),
        (
            "import importlib\n\ndef poke():\n    importlib.import_module('untaped_shelf.lamps')\n",
            "untaped_shelf.lamps",
        ),
        (
            "import importlib\n\ndef poke():\n    importlib.import_module('untaped_acme.late')\n",
            "untaped_shelf.api.lamp",
        ),
        (
            "from untaped_shelf.api import Shade\n\ndef poke():\n    Shade(tint='red', hue=3)\n",
            "untaped_shelf.api.Shade(hue=)",
        ),
    ],
    ids=["module-attribute", "missing-module", "from-import", "extra-field"],
)
def test_each_kind_of_missing_name_says_what_it_is(site: Site, poke: str, spelled: str) -> None:
    found = _install(site, poke)
    late = site.root / "untaped-acme" / "untaped_acme" / "late.py"
    late.write_text("from untaped_shelf.api import lamp\n", encoding="utf-8")
    result = _invoke(found, "acme", "poke")
    assert result.exit_code == 4, result.output
    assert f"it uses {spelled}, which {_OUT_OF_DATE}" in result.stderr


def test_no_major_since_the_floor_keeps_the_traceback_with_a_line_before_it(site: Site) -> None:
    found = _install(
        site, "from untaped_shelf.api import bulb\n\ndef poke():\n    bulb().dim()\n", shelf="3.2"
    )
    result = _invoke(found, "acme", "poke")
    assert isinstance(result.exception, AttributeError)
    assert result.stderr.count("warning: acme:") == 1  # the root's boundary and the mount's
    assert result.stderr.splitlines()[0] == (
        "warning: acme: it uses untaped_shelf.api.Bulb.dim, which untaped-shelf 3.2 doesn't have: "
        "untaped-acme may need a newer untaped-shelf than it declares"
    )


@pytest.mark.parametrize(
    "poke",
    [
        "from untaped_acme.own import Gadget\n\ndef poke():\n    Gadget().spin()\n",
        "import json\n\ndef poke():\n    json.gone()\n",
        "from untaped_shelf.api import Shade\n\n"
        "def poke():\n    Shade.model_validate({'hue': 3})\n",
        "def poke():\n    None.spin()\n",
        "from untaped_shelf.api import Knob\n\ndef poke():\n    Knob().twist\n",
        "from untaped.sdk import key\nfrom untaped_acme.plugin import AcmeSettings\n\n"
        "def poke():\n    key(AcmeSettings).sever\n",
        "from untaped_shelf.api import Frame\n\ndef poke():\n    Frame(shade={'hue': 3})\n",
        "import untaped_shelf.fittings\n\ndef poke():\n    untaped_shelf.fittings.kinds\n",
        "from untaped_shelf import api\n\ndef poke():\n    getattr(api, 'ghost')\n",
        "import importlib\n\ndef poke():\n    importlib.import_module('untaped.plugins.lamp')\n",
    ],
    ids=[
        "own-object",
        "library",
        "user-data",
        "none",
        "class-hook",
        "setting-key",
        "nested-field",
        "unimported-submodule",
        "listed-but-broken",
        "namespace-module",
    ],
)
def test_anything_else_stays_the_error_it_is(site: Site, poke: str) -> None:
    found = _install(site, poke)
    result = _invoke(found, "acme", "poke")
    assert result.exception is not None
    assert result.exit_code != 4
    assert "can't run this" not in result.stderr, result.stderr


def test_a_name_gone_while_the_plugin_loads_disables_it_as_out_of_date(site: Site) -> None:
    found = _install(site, "def poke():\n    pass\n")
    plugin = site.root / "untaped-acme" / "untaped_acme" / "plugin.py"
    plugin.write_text(
        "import importlib\n\nimportlib.import_module('untaped_shelf.lamps')\n"
        + plugin.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = _invoke(found, "acme", "poke")
    assert result.exit_code == 4
    assert (
        "quarantined [uses-missing]: untaped-acme 1.0: untaped_acme/plugin.py:3 uses "
        f"untaped_shelf.lamps, which {_OUT_OF_DATE}; upgrade untaped-acme, or remove it"
    ) in " ".join(result.stderr.split())


def test_a_run_of_another_composition_is_judged_by_nothing(site: Site) -> None:
    found = _install(site, "def poke():\n    pass\n")
    assert _invoke(found, "acme", "poke").exit_code == 0
    assert bootstrap.attached() is None  # outside the root's run: the testing kit's, a check's
