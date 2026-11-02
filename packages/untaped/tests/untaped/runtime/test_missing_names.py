"""A name a plugin reaches that untaped or another plugin doesn't have: an error saying so.

The plugins are real installs: ``untaped-shelf`` exports ``Bulb`` (defined in
a private module) and ``Shade`` (a model that forbids extra fields) from its
``api``; ``untaped-acme``'s ``poke`` command runs what each test writes (a
name on a returned object, a lookup by ``getattr`` or ``import_module``).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent
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
    "untaped_shelf/extras/lights.py": "",
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


_FACTS = "untaped-shelf 4.0 doesn't have (untaped-acme requires untaped-shelf>=3.0)"
_FIX = "upgrade untaped-acme or untaped-shelf, or remove untaped-acme"


def test_a_member_gone_from_a_returned_object_names_it_by_its_public_module(site: Site) -> None:
    found = _install(site, "from untaped_shelf.api import bulb\n\ndef poke():\n    bulb().dim()\n")
    result = _invoke(found, "acme", "poke")
    assert result.exit_code == 4, result.output
    assert result.stderr.splitlines() == [
        "error: acme can't run this: untaped-acme 1.0 uses untaped_shelf.api.Bulb.dim, which "
        f"{_FACTS}",
        f"hint: {_FIX} (--verbose shows where)",
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
            "from untaped_shelf.api import Shade\n\ndef poke():\n    Shade.from_hex('fff')\n",
            "untaped_shelf.api.Shade.from_hex",
        ),
    ],
    ids=["module-attribute", "missing-module", "from-import", "model-class"],
)
def test_each_kind_of_missing_name_says_what_it_is(site: Site, poke: str, spelled: str) -> None:
    found = _install(site, poke)
    acme = site.root / "untaped-acme" / "untaped_acme"
    (acme / "late.py").write_text("from untaped_shelf.api import lamp\n", encoding="utf-8")
    result = _invoke(found, "acme", "poke")
    assert result.exit_code == 4, result.output
    assert f"untaped-acme 1.0 uses {spelled}, which {_FACTS}" in result.stderr


@pytest.mark.parametrize(
    ("module", "spelled"),
    [("untaped.sdk", "untaped.sdk.gone"), ("untaped.sdk.storage", "untaped.sdk.storage.gone")],
    ids=["root", "group"],
)
def test_a_name_the_sdk_s_lazy_modules_lack_says_what_it_is(
    site: Site, module: str, spelled: str
) -> None:
    found = _install(site, f"import {module} as sdk\n\ndef poke():\n    sdk.gone\n")
    result = _invoke(found, "acme", "poke")
    assert result.exit_code == 4, result.output
    assert f"untaped-acme 1.0 uses {spelled}, which untaped " in result.stderr


def test_with_no_major_since_the_floor_it_is_the_same_error(site: Site) -> None:
    found = _install(
        site, "from untaped_shelf.api import bulb\n\ndef poke():\n    bulb().dim()\n", shelf="3.2"
    )
    result = _invoke(found, "acme", "poke")
    assert result.exit_code == 4, result.output
    assert result.stderr.splitlines()[0] == (
        "error: acme can't run this: untaped-acme 1.0 uses untaped_shelf.api.Bulb.dim, which "
        "untaped-shelf 3.2 doesn't have (untaped-acme requires untaped-shelf>=3.0)"
    )


@pytest.mark.parametrize(
    "poke",
    [
        "from untaped_acme.own import Gadget\n\ndef poke():\n    Gadget().spin()\n",
        "import json\n\ndef poke():\n    json.gone()\n",
        "from untaped_shelf.api import Shade\n\n"
        "def poke():\n    Shade.model_validate({'hue': 3})\n",
        "from untaped_shelf.api import Shade\n\ndef poke():\n    Shade(tint='red', hue=3)\n",
        "def poke():\n    None.spin()\n",
        "from untaped_shelf.api import Knob\n\ndef poke():\n    Knob().twist\n",
        "from untaped.sdk import key\nfrom untaped_acme.plugin import AcmeSettings\n\n"
        "def poke():\n    key(AcmeSettings).sever\n",
        "from untaped_shelf.api import Frame\n\ndef poke():\n    Frame(shade={'hue': 3})\n",
        "import untaped_shelf.fittings\n\ndef poke():\n    untaped_shelf.fittings.kinds\n",
        "from untaped_shelf import api\n\ndef poke():\n    getattr(api, 'ghost')\n",
        "import importlib\n\ndef poke():\n    importlib.import_module('untaped.plugins.lamp')\n",
        "import importlib\n\ndef poke():\n    importlib.import_module('untaped_acme.nested')\n",
        "import untaped_shelf.extras\n\ndef poke():\n    getattr(untaped_shelf.extras, 'lamp')\n",
        "from untaped_shelf.api import Shade\n\ndef poke():\n    getattr(Shade, 'tint')\n",
        "import untaped\n\ndef poke():\n    getattr(untaped, 'plugins')\n",
    ],
    ids=[
        "own-object",
        "library",
        "user-data",
        "extra-field",
        "none",
        "class-hook",
        "setting-key",
        "nested-field",
        "unimported-submodule",
        "listed-but-broken",
        "namespace-module",
        "namespace-from-import",
        "namespace-attribute",
        "model-field-on-class",
        "untaped-top",
    ],
)
def test_anything_else_stays_the_error_it_is(site: Site, poke: str) -> None:
    found = _install(site, poke)
    nested = site.root / "untaped-acme" / "untaped_acme" / "nested.py"
    nested.write_text("from untaped_shelf.extras import lamp\n", encoding="utf-8")
    result = _invoke(found, "acme", "poke")
    assert result.exception is not None
    assert result.exit_code != 4
    assert "can't run this" not in result.stderr, result.stderr


def test_a_name_missing_while_the_plugin_loads_disables_it(site: Site) -> None:
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
        f"untaped_shelf.lamps, which {_FACTS}; {_FIX}"
    ) in " ".join(result.stderr.split())


def test_a_run_of_another_composition_is_judged_by_nothing(site: Site) -> None:
    found = _install(site, "def poke():\n    pass\n")
    assert _invoke(found, "acme", "poke").exit_code == 0
    assert bootstrap.attached() is None  # outside the root's run: the testing kit's, a check's


_LAMPS = {
    "untaped_shelf/lamps.py": '''
        """Shelf's contract, and the command asking it."""

        from abc import abstractmethod

        from untaped.sdk import Command
        from untaped.sdk.contracts import Contract, gather


        class Lamps(Contract):
            """Who lights lamps."""

            @abstractmethod
            def glow(self) -> str:
                """How it glows."""


        class Glow(Command):
            """Ask every provider how it glows."""

            def run(self) -> None:
                for errored in gather(Lamps.glow)().errored:
                    self.ctx.diag.failure(errored.error, item=errored.plugin)
        ''',
}

_GLOWING = '''
    """``acme``, a provider of shelf's ``Lamps`` whose answer dims a bulb."""

    from pydantic import BaseModel, ConfigDict

    from untaped.sdk import Plugin
    from untaped.sdk.contracts import Provider
    from untaped_shelf.api import bulb
    from untaped_shelf.lamps import Lamps


    class AcmeSettings(BaseModel):
        model_config = ConfigDict(frozen=True)


    class AcmeLamps(Lamps, Provider[AcmeSettings]):
        def glow(self) -> str:
            return bulb().dim()


    class Acme(Plugin):
        """Lights lamps."""

        settings = AcmeSettings
        fills = (AcmeLamps,)
    '''


def test_a_provider_s_answer_says_it_as_a_command_does(site: Site) -> None:
    shelf_plugin = dedent(_SHELF["untaped_shelf/plugin.py"]).replace(
        "from untaped.sdk import Plugin\n",
        "from untaped.sdk import Plugin\nfrom untaped_shelf.lamps import Glow, Lamps\n",
    )
    shelf_plugin = shelf_plugin.replace(
        "settings = ShelfSettings\n",
        "settings = ShelfSettings\n    commands = (Glow,)\n    offers = (Lamps,)\n",
    )
    shelf = site.install(
        "untaped-shelf",
        {**_SHELF, **_LAMPS, "untaped_shelf/plugin.py": shelf_plugin},
        at="4.0",
    )
    acme = site.install(
        "untaped-acme",
        {"untaped_acme/__init__.py": "", "untaped_acme/plugin.py": _GLOWING},
        requires=("untaped-shelf>=3.0",),
    )
    result = _invoke([acme, shelf], "shelf", "glow")
    assert result.exit_code != 0
    assert (
        f"acme can't run this: untaped-acme 1.0 uses untaped_shelf.api.Bulb.dim, which {_FACTS}"
    ) in " ".join(result.stderr.split()), result.stderr
