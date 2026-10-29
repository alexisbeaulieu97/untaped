"""``provides-requirement``: a provider declares the ranges its offers rely on."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.metadata import version

import pytest

from core_test_support.plugin_check.support import Install
from untaped.core.commands.plugin_new import _range
from untaped.core.plugin_check.rules import plugin_violations
from untaped.runtime.registry import PluginCandidate


def _owner(plugin: str, contract: str) -> dict[str, str]:
    """Plugin ``plugin`` offering contract ``contract`` from its ``api`` module."""
    package = f"untaped_{plugin}"
    return {
        f"{package}/__init__.py": "",
        f"{package}/api.py": f'''
            """What a provider may import."""

            from untaped.sdk.contracts import Contract


            class {contract}(Contract):
                """Where {plugin} items come from."""

                def items(self) -> list[str]:
                    """Every item."""
                    raise NotImplementedError
            ''',
        f"{package}/plugin.py": f'''
            """The ``{plugin}`` plugin class."""

            from pydantic import BaseModel, ConfigDict

            from untaped.sdk import Plugin
            from {package}.api import {contract}


            class Settings(BaseModel):
                """Nothing to configure."""

                model_config = ConfigDict(frozen=True)


            class Owner(Plugin):
                """Where {plugin} items come from."""

                settings = Settings
                offers = ({contract},)
            ''',
    }


_PLUGIN = {
    **_owner("shelf", "Shelf"),
    **_owner("kiosk", "Kiosk"),
    "untaped_lend/__init__.py": '"""A provider for shelf and kiosk."""\n',
    "untaped_lend/errors.py": '"""Errors."""\n',
    "untaped_lend/plugin.py": '''
        """The ``lend`` plugin class."""

        from pydantic import BaseModel, ConfigDict

        from untaped.sdk import Plugin
        from untaped.sdk.contracts import Provider
        from untaped_kiosk.api import Kiosk
        from untaped_shelf.api import Shelf


        class LendSettings(BaseModel):
            """Nothing to configure."""

            model_config = ConfigDict(frozen=True)


        class LendShelf(Shelf, Provider):
            """Lent books."""

            def items(self) -> list[str]:
                return []


        class LendKiosk(Kiosk, Provider):
            """Lent papers."""

            def items(self) -> list[str]:
                return []


        class Lend(Plugin):
            """A provider for shelf and kiosk."""

            settings = LendSettings
            fills = (LendShelf, LendKiosk)
        ''',
}


def _owners(kiosk: str = "untaped-kiosk") -> list[PluginCandidate]:
    """The owners as installed, ``kiosk`` from distribution ``kiosk``."""
    return [
        PluginCandidate(
            distribution="untaped-shelf", name="shelf", target="untaped_shelf.plugin:Owner"
        ),
        PluginCandidate(distribution=kiosk, name="kiosk", target="untaped_kiosk.plugin:Owner"),
    ]


_CORE = f"untaped{_range(version('untaped'))}"
_RANGES = (_CORE, "untaped-shelf>=1,<2", "untaped-kiosk>=3,<4")


def _rule(install: Install, requires: Sequence[str], kiosk: str = "untaped-kiosk") -> list[str]:
    install(_PLUGIN)
    candidate = PluginCandidate(
        distribution="untaped-lend",
        name="lend",
        target="untaped_lend.plugin:Lend",
        requires_dist=tuple(requires),
    )
    found = plugin_violations("lend", candidates=[candidate, *_owners(kiosk)])
    return [line for line in found if "::provides-requirement::" in line]


def test_ranges_for_untaped_and_each_owner_pass(install: Install) -> None:
    assert _rule(install, _RANGES) == []


def test_an_owner_behind_a_marker_naming_no_extra_is_plain(install: Install) -> None:
    requires = (*_RANGES[:2], "untaped-kiosk>=3,<4; python_version >= '3.10'")
    assert _rule(install, requires) == []


@pytest.mark.parametrize(
    "kiosk",
    [
        "",
        "untaped-kiosk>=3,<4; extra == 'kiosk'",
        "untaped-kiosk>=3,<4; extra == 'kiosk' and 'x' in extras",
    ],
    ids=["absent", "extra", "unfillable-marker"],
)
def test_an_owner_not_in_the_plain_dependencies_is_a_violation(
    install: Install, kiosk: str
) -> None:
    requires = [*_RANGES[:2], *([kiosk] if kiosk else [])]
    assert _rule(install, requires) == [
        "lend::provides-requirement::fills a contract of kiosk, which it does not require "
        "(add untaped-kiosk>=M,<M+1 to its dependencies)"
    ]


def test_an_owner_without_a_range_is_a_violation(install: Install) -> None:
    requires = (_CORE, "untaped-shelf", _RANGES[2])
    assert _rule(install, requires) == [
        "lend::provides-requirement::requires untaped-shelf with no version range"
    ]


@pytest.mark.parametrize("core", [None, "untaped"], ids=["absent", "no-range"])
def test_untaped_without_a_range_is_a_violation(install: Install, core: str | None) -> None:
    requires = [*([] if core is None else [core]), *_RANGES[1:]]
    assert _rule(install, requires) == [
        "lend::provides-requirement::requires no untaped version range (untaped>=M,<M+1)"
    ]


def test_the_owner_is_named_by_its_installed_distribution(install: Install) -> None:
    requires = (*_RANGES[:2], "acme-kiosk>=1,<2")
    assert _rule(install, requires, kiosk="Acme_Kiosk") == []


def test_a_plugin_without_fills_has_nothing_to_declare(install: Install) -> None:
    install(
        {
            "untaped_plain/__init__.py": '"""Plain."""\n',
            "untaped_plain/errors.py": '"""Errors."""\n',
            "untaped_plain/plugin.py": """
                \"\"\"The plain plugin class.\"\"\"

                from pydantic import BaseModel, ConfigDict

                from untaped.sdk import Plugin


                class PlainSettings(BaseModel):
                    \"\"\"Nothing to configure.\"\"\"

                    model_config = ConfigDict(frozen=True)


                class Plain(Plugin):
                    \"\"\"Plain.\"\"\"

                    settings = PlainSettings
                """,
        }
    )
    candidate = PluginCandidate(
        distribution="untaped-plain", name="plain", target="untaped_plain.plugin:Plain"
    )
    assert plugin_violations("plain", candidates=[candidate]) == []
