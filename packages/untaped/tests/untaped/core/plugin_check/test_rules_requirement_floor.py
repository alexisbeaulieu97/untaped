"""``requirement-floor``: a plugin's requirements on untaped and on other plugins are floors."""

from __future__ import annotations

import json
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

import pytest

from core_test_support.plugin_check.support import Install
from untaped.core.plugin_check import rules
from untaped.core.plugin_check.rules import plugin_violations
from untaped.runtime.registry import PluginCandidate
from untaped.runtime.requirements import floor_for


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
            distribution="untaped-shelf",
            name="shelf",
            target="untaped_shelf.plugin:Owner",
            distribution_version="1.2.0",
        ),
        PluginCandidate(
            distribution=kiosk,
            name="kiosk",
            target="untaped_kiosk.plugin:Owner",
            distribution_version="3.0.0",
        ),
    ]


_CORE = f"untaped{floor_for(version('untaped'))}"
_DECLARE_CORE = f"requires no untaped: declare {_CORE}"
_FLOORS = (_CORE, "untaped-shelf>=1", "untaped-kiosk>=3")


def _rule(install: Install, requires: Sequence[str], kiosk: str = "untaped-kiosk") -> list[str]:
    install(_PLUGIN)
    candidate = PluginCandidate(
        distribution="untaped-lend",
        name="lend",
        target="untaped_lend.plugin:Lend",
        distribution_version="0.1.0",
        requires_dist=tuple(requires),
    )
    found = plugin_violations("lend", candidates=[candidate, *_owners(kiosk)])
    return [line for line in found if "::requirement-floor::" in line]


def test_floors_for_untaped_and_each_owner_pass(install: Install) -> None:
    assert _rule(install, _FLOORS) == []


@pytest.mark.parametrize(
    "extra",
    [
        "untaped-shelf>=1,!=1.3.0",
        "untaped-kiosk>=3; python_version >= '3.10'",
        "untaped-kiosk @ https://example.com/untaped_kiosk-3.0.0-py3-none-any.whl",
        "httpx>=0.28,<1",
        "other-lib==1.0; extra == 'other'",
    ],
    ids=["not-equal", "marker", "url", "a-library", "an-optional-library"],
)
def test_what_is_not_an_upper_limit_on_a_plugin_passes(install: Install, extra: str) -> None:
    assert _rule(install, (_CORE, *_FLOORS[1:], extra)) == []


_WHY = "one stale plugin would hold back every install that has it"


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("untaped-shelf>=1,<2", f"has an upper limit: {_WHY}; declare untaped-shelf>=1"),
        ("untaped-shelf~=1.2", f"has an upper limit: {_WHY}; declare untaped-shelf>=1.2"),
        ("untaped-shelf==1.*", f"has an upper limit: {_WHY}; declare untaped-shelf>=1"),
        ("untaped-shelf<2", f"has an upper limit: {_WHY}; declare untaped-shelf>=1.2"),
        (
            "untaped-shelf==1.2.0",
            "is an exact pin to a package not built in this workspace: "
            "declare untaped-shelf>=1.2.0",
        ),
        ("untaped-shelf", "has no floor: declare untaped-shelf>=1.2"),
        ("untaped-shelf!=1.3.0", "has no floor: declare untaped-shelf>=1.2"),
        (
            "untaped-shelf>=1,<2; extra == 'shelf'",
            f"has an upper limit: {_WHY}; declare untaped-shelf>=1",
        ),
    ],
    ids=["range", "compatible", "wildcard", "below", "pin", "bare", "not-equal-alone", "optional"],
)
def test_a_requirement_on_a_plugin_that_is_not_a_floor_is_a_violation(
    install: Install, line: str, problem: str
) -> None:
    requires = (_CORE, line, "untaped-kiosk>=3") if "extra" not in line else (*_FLOORS, line)
    assert _rule(install, requires) == [f"lend::requirement-floor::{line} {problem}"]


def test_an_upper_limit_on_untaped_is_a_violation(install: Install) -> None:
    core = f"untaped{floor_for(version('untaped'))},<99"
    assert _rule(install, (core, *_FLOORS[1:])) == [
        f"lend::requirement-floor::{core} has an upper limit: {_WHY}; "
        f"declare untaped{floor_for(version('untaped'))}"
    ]


def test_a_pin_between_packages_of_one_workspace_is_allowed(
    install: Install, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    roots = {"untaped-lend": tmp_path, "untaped-shelf": tmp_path, "untaped-kiosk": None}
    monkeypatch.setattr(rules, "_workspace_root", lambda name: roots.get(name))
    assert _rule(install, (_CORE, "untaped-shelf==1.2.0", "untaped-kiosk>=3")) == []
    assert _rule(install, (_CORE, "untaped-shelf>=1", "untaped-kiosk==3.0.0")) == [
        "lend::requirement-floor::untaped-kiosk==3.0.0 is an exact pin to a package not "
        "built in this workspace: declare untaped-kiosk>=3.0.0"
    ]


def _editable(site: Path, name: str, source: Path) -> None:
    """An installed ``name`` whose record says it is an editable install of ``source``."""
    info = site / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    direct = {"url": source.as_uri(), "dir_info": {"editable": True}}
    (info / "direct_url.json").write_text(json.dumps(direct), encoding="utf-8")


def test_the_workspace_is_read_from_each_editable_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, other = tmp_path / "repo", tmp_path / "elsewhere"
    (workspace / "packages" / "a" / "src").mkdir(parents=True)
    (workspace / "packages" / "b").mkdir(parents=True)
    (other / "c").mkdir(parents=True)
    (workspace / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers = ["packages/*"]\n')
    (workspace / "packages" / "a" / "pyproject.toml").write_text('[project]\nname = "a"\n')
    site = tmp_path / "site"
    _editable(site, "untaped-wsa", workspace / "packages" / "a")
    _editable(site, "untaped-wsb", workspace / "packages" / "b")
    _editable(site, "untaped-wsc", other / "c")
    monkeypatch.syspath_prepend(str(site))
    assert rules._workspace_root("untaped-wsa") == workspace
    assert rules._one_workspace("untaped-wsa", "untaped-wsb")
    assert not rules._one_workspace("untaped-wsa", "untaped-wsc")
    assert not rules._one_workspace("untaped-wsa", "untaped-nowhere-installed")


@pytest.mark.parametrize(
    "kiosk",
    [
        "",
        "untaped-kiosk>=3; extra == 'kiosk'",
        "untaped-kiosk>=3; extra == 'kiosk' and 'x' in extras",
    ],
    ids=["absent", "extra", "unfillable-marker"],
)
def test_an_owner_not_in_the_plain_dependencies_is_a_violation(
    install: Install, kiosk: str
) -> None:
    requires = [*_FLOORS[:2], *([kiosk] if kiosk else [])]
    assert _rule(install, requires) == [
        "lend::requirement-floor::fills a contract of kiosk, which it does not require "
        "(add untaped-kiosk>=3.0 to its dependencies)"
    ]


def test_no_untaped_requirement_is_a_violation(install: Install) -> None:
    assert _rule(install, _FLOORS[1:]) == [f"lend::requirement-floor::{_DECLARE_CORE}"]


def test_the_owner_is_named_by_its_installed_distribution(install: Install) -> None:
    requires = (*_FLOORS[:2], "acme-kiosk>=1")
    assert _rule(install, requires, kiosk="Acme_Kiosk") == []


def test_a_plugin_without_fills_needs_untaped_only(install: Install) -> None:
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

    def plain(*requires: str) -> list[str]:
        candidate = PluginCandidate(
            distribution="untaped-plain",
            name="plain",
            target="untaped_plain.plugin:Plain",
            requires_dist=requires,
        )
        found = plugin_violations("plain", candidates=[candidate])
        return [line for line in found if "::requirement-floor::" in line]

    assert plain(_CORE) == []
    assert plain("httpx>=0.28") == [f"plain::requirement-floor::{_DECLARE_CORE}"]
    assert plain() == []  # composed without an install: no requirements to read
