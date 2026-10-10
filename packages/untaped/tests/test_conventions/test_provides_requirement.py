"""``provides-requirement``: a provider declares the ranges its offers rely on."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from test_conventions.support import Install
from untaped.conventions import plugin_violations
from untaped.plugins.registry import PluginCandidate

_PLUGIN = {
    "untaped_lend/__init__.py": '''
        """A provider for shelf and kiosk."""

        from untaped.sdk import PluginSpec

        SPEC = PluginSpec(name="lend", provides={"shelf": tuple, "kiosk": tuple})
        ''',
    "untaped_lend/errors.py": '''
        """Errors."""
        ''',
}

_RANGES = (
    "untaped>=10,<11",
    "untaped-shelf>=1,<2; extra == 'shelf'",
    "untaped-kiosk>=3,<4; extra == 'kiosk'",
)


def _rule(install: Install, requires: Sequence[str], *others: PluginCandidate) -> list[str]:
    install(_PLUGIN)
    candidate = PluginCandidate(
        distribution="untaped-lend",
        name="lend",
        target="untaped_lend:SPEC",
        requires_dist=tuple(requires),
    )
    found = plugin_violations("lend", candidates=[candidate, *others])
    return [line for line in found if "::provides-requirement::" in line]


def test_ranges_for_untaped_and_each_owner_extra_pass(install: Install) -> None:
    assert _rule(install, _RANGES) == []


def test_an_owner_outside_an_extra_is_a_violation(install: Install) -> None:
    requires = ("untaped>=10,<11", "untaped-shelf>=1,<2", "untaped-kiosk>=3,<4; extra == 'kiosk'")
    assert _rule(install, requires) == [
        "lend::provides-requirement::provides for shelf but has no 'shelf' extra "
        "requiring untaped-shelf"
    ]


def test_an_extra_without_a_range_is_a_violation(install: Install) -> None:
    requires = ("untaped>=10,<11", "untaped-shelf; extra == 'shelf'", _RANGES[2])
    assert _rule(install, requires) == [
        "lend::provides-requirement::its 'shelf' extra requires untaped-shelf with no version range"
    ]


@pytest.mark.parametrize("core", [None, "untaped"], ids=["absent", "no-range"])
def test_untaped_without_a_range_is_a_violation(install: Install, core: str | None) -> None:
    requires = [*([] if core is None else [core]), *_RANGES[1:]]
    assert _rule(install, requires) == [
        "lend::provides-requirement::requires no untaped version range (untaped>=M,<M+1)"
    ]


def test_the_owner_is_named_by_its_installed_distribution(install: Install) -> None:
    kiosk = PluginCandidate(distribution="Acme_Kiosk", name="kiosk", target="acme_kiosk:SPEC")
    requires = (*_RANGES[:2], "acme-kiosk>=1,<2; extra == 'kiosk'")
    assert _rule(install, requires, kiosk) == []


def test_a_plugin_without_provides_has_nothing_to_declare(install: Install) -> None:
    install(
        {
            "untaped_plain/__init__.py": """
                from untaped.sdk import PluginSpec

                SPEC = PluginSpec(name="plain")
                """,
            "untaped_plain/errors.py": '"""Errors."""\n',
        }
    )
    candidate = PluginCandidate(
        distribution="untaped-plain", name="plain", target="untaped_plain:SPEC"
    )
    assert plugin_violations("plain", candidates=[candidate]) == []


def test_a_marker_the_environment_cannot_fill_is_reported_not_raised(install: Install) -> None:
    requires = (*_RANGES[:2], "untaped-kiosk>=3,<4; extra == 'kiosk' and 'x' in extras")
    assert _rule(install, requires) == [
        "lend::provides-requirement::provides for kiosk but has no 'kiosk' extra "
        "requiring untaped-kiosk"
    ]
