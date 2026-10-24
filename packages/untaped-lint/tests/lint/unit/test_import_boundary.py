"""Import boundary: plugin code reaches core only through ``untaped.sdk``.

The checked plugin's files are written under a temporary directory; every
plugin, the checked one included, is an ``untaped.installed_plugin`` record, so each
case controls the packages, distributions and requirements.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from textwrap import dedent

import pytest

from untaped.core import surface
from untaped.sdk import InstalledPlugin
from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions import file_lines
from untaped_lint.providers.conventions.imports import SDK_MODULES, import_boundary_violations


def plugin(
    name: str,
    *,
    dist: str = "",
    package: str = "",
    requires: tuple[str, ...] = (),
    quarantined: bool = False,
) -> InstalledPlugin:
    """A record; ``dist`` and ``package`` default to ``name``."""
    return InstalledPlugin(
        name=name,
        status="quarantined" if quarantined else "ready",
        distribution=dist or name,
        version="1.0",
        package=package or name,
        requires=requires,
    )


Check = Callable[..., list[str]]


@pytest.fixture
def check(tmp_path: Path) -> Check:
    """``check(files, *others, demo=...)``: the boundary lines of plugin ``demo``."""

    def run(
        files: dict[str, str], *others: InstalledPlugin, demo: InstalledPlugin | None = None
    ) -> list[str]:
        subject = demo or plugin("demo")
        assert subject.package is not None
        source_dir = tmp_path.joinpath(*subject.package.split("."))
        for name, body in {"__init__.py": "", **files}.items():
            path = source_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dedent(body), encoding="utf-8")
        found = list(source_files(source_dir))
        return import_boundary_violations(subject, source_dir, found, plugins=[subject, *others])

    return run


def test_core_imports_other_than_the_sdk_are_violations(check: Check) -> None:
    assert check({"cli/__init__.py": "from untaped.runtime import bootstrap\n"}) == [
        "demo/cli/__init__.py:1::import-boundary::imports untaped.runtime; use untaped.sdk"
    ]


def test_sdk_modules_the_surface_does_not_promise_are_violations(check: Check) -> None:
    source = "from untaped.sdk._cli import echo\nimport untaped.sdk.storage.cache\n"
    assert check({"cli/__init__.py": source}) == [
        "demo/cli/__init__.py:1::import-boundary::imports untaped.sdk._cli; use untaped.sdk",
        "demo/cli/__init__.py:2::import-boundary::imports untaped.sdk.storage.cache; "
        "use untaped.sdk",
    ]


def test_the_sdk_modules_are_the_ones_the_surface_promises() -> None:
    assert frozenset(surface.SDK_MODULES) == SDK_MODULES


def test_bare_untaped_and_import_statements_are_violations(check: Check) -> None:
    source = "import untaped\nimport untaped.runtime.settings as s\nfrom untaped.core import api\n"
    assert check({"cli/__init__.py": source}) == [
        "demo/cli/__init__.py:1::import-boundary::imports untaped; use untaped.sdk",
        "demo/cli/__init__.py:2::import-boundary::imports untaped.runtime.settings; "
        "use untaped.sdk",
        "demo/cli/__init__.py:3::import-boundary::imports untaped.core; use untaped.sdk",
    ]


def test_the_sdk_modules_and_own_modules_are_allowed(check: Check) -> None:
    source = (
        "from untaped.sdk import create_app\n"
        "from untaped.sdk.testing import check_plugin\n"
        "from untaped.sdk.testing import git\n"
        "from demo.domain import x\n"
        "import json\n"
    )
    assert check({"cli/__init__.py": source, "domain/__init__.py": "x = 1\n"}) == []


def test_function_level_and_type_checking_imports_count(check: Check) -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from untaped.sdk._cli import a\n"
        "def f():\n"
        "    from untaped.runtime.settings import b\n"
    )
    assert check({"cli/__init__.py": source}) == [
        "demo/cli/__init__.py:3::import-boundary::imports untaped.sdk._cli; use untaped.sdk",
        "demo/cli/__init__.py:5::import-boundary::imports untaped.runtime.settings; "
        "use untaped.sdk",
    ]


def test_a_waiver_suppresses_one_line(check: Check) -> None:
    source = (
        "import untaped.sdk._cli  # untaped: allow import-boundary\n"
        "import untaped.runtime.settings  # untaped: allow lint.import-boundary\n"
        "import untaped.runtime.sink\n"
    )
    assert check({"cli/__init__.py": source}) == [
        "demo/cli/__init__.py:3::import-boundary::imports untaped.runtime.sink; use untaped.sdk"
    ]


def test_another_plugin_only_through_its_api(check: Check) -> None:
    found = check(
        {"cli/__init__.py": "from other.api import x\nfrom other.domain import y\n"},
        plugin("other"),
        demo=plugin("demo", requires=("other",)),
    )
    assert found == ["demo/cli/__init__.py:2::import-boundary::imports other.domain; use other.api"]


def test_importing_the_api_module_by_name_is_allowed(check: Check) -> None:
    found = check(
        {"cli/__init__.py": "from other import api\n"},
        plugin("other"),
        demo=plugin("demo", requires=("other",)),
    )
    assert found == []


def test_an_api_import_needs_a_declared_dependency(check: Check) -> None:
    assert check({"cli/__init__.py": "from other.api import x\n"}, plugin("other")) == [
        "demo/cli/__init__.py:1::import-boundary::"
        "imports other.api but its distribution does not depend on other's"
    ]


def test_distribution_names_compare_canonically(check: Check) -> None:
    found = check(
        {"cli/__init__.py": "from other.api import x\n"},
        plugin("other", dist="Untaped_Other"),
        demo=plugin("demo", requires=("untaped-other",)),
    )
    assert found == []


def test_plugins_in_one_distribution_may_import_each_others_api(check: Check) -> None:
    found = check(
        {"cli/__init__.py": "from other.api import x\n"},
        plugin("other", dist="suite"),
        demo=plugin("demo", dist="suite"),
    )
    assert found == []


def test_a_quarantined_plugin_still_counts_as_a_plugin(check: Check) -> None:
    found = check(
        {"cli/__init__.py": "from other.domain import y\n"},
        plugin("other", quarantined=True),
        demo=plugin("demo", requires=("other",)),
    )
    assert found == ["demo/cli/__init__.py:1::import-boundary::imports other.domain; use other.api"]


def test_a_plugin_nested_under_untaped_is_a_plugin_not_core(check: Check) -> None:
    package = "untaped.plugins.other"
    found = check(
        {"cli/__init__.py": f"def f():\n    from {package}.domain import y\n"},
        plugin("other", package=package),
        demo=plugin("demo", requires=("other",)),
    )
    assert found == [
        f"demo/cli/__init__.py:2::import-boundary::imports {package}.domain; use {package}.api"
    ]


def test_relative_imports_within_the_own_package_are_allowed(check: Check) -> None:
    files = {
        "cli/__init__.py": "from . import helpers\nfrom ..domain import x\n",
        "cli/helpers.py": "",
        "domain/__init__.py": "x = 1\n",
        "application/__init__.py": "from .ports import p\n",
        "application/ports.py": "p = 1\n",
    }
    assert check(files) == []


def test_a_relative_import_into_a_sibling_plugin_is_a_violation(check: Check) -> None:
    found = check(
        {"cli/__init__.py": "from ...other.domain import y\n"},
        plugin("other", package="suite.other"),
        demo=plugin("demo", package="suite.demo", requires=("other",)),
    )
    assert found == [
        "demo/cli/__init__.py:1::import-boundary::imports suite.other.domain; use suite.other.api"
    ]


def test_a_record_without_a_package_has_nothing_to_check(tmp_path: Path) -> None:
    subject = InstalledPlugin(name="demo", status="quarantined", distribution="demo", version="1.0")
    assert import_boundary_violations(subject, tmp_path, [], plugins=[subject]) == []


def test_file_lines_run_the_boundary_for_the_named_plugin(tmp_path: Path) -> None:
    source_dir = tmp_path / "demo"
    source_dir.mkdir()
    (source_dir / "__init__.py").write_text(
        '"""Demo."""\nimport untaped.runtime\nfrom other.api import x\n', "utf-8"
    )
    core = "demo/__init__.py:2::import-boundary::imports untaped.runtime; use untaped.sdk"
    undeclared = (
        "demo/__init__.py:3::import-boundary::"
        "imports other.api but its distribution does not depend on other's"
    )
    found = file_lines("demo", source_dir, plugin="demo", plugins=[plugin("other")])
    assert {core, undeclared} <= set(found)  # no record of demo: it requires nothing
    declared = [plugin("demo", requires=("other",)), plugin("other")]
    found = file_lines("demo", source_dir, plugin="demo", plugins=declared)
    assert core in found
    assert undeclared not in found
