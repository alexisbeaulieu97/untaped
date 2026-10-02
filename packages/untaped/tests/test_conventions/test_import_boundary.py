"""Import boundary: capability code reaches core only through ``untaped.sdk``.

Capabilities are synthetic packages written into a temporary site and
discovered through explicit candidates, so each case controls the
distributions and their ``Requires-Dist``.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from textwrap import dedent

import pytest

from test_conventions.support import Install
from untaped.capabilities.registry import ProviderCandidate
from untaped.conventions import capability_violations
from untaped.testing import provider_candidate

_INIT = """
    from __future__ import annotations

    from pydantic import BaseModel, ConfigDict

    from untaped.sdk import CapabilitySpec


    class Settings(BaseModel):
        model_config = ConfigDict(frozen=True)


    def build_app():
        from untaped.sdk import create_app

        return create_app(name="{name}", help="Demo.")


    SPEC = CapabilitySpec(
        name="{name}", app_factory=build_app, config_section="{name}", profile_model=Settings
    )


    def provider() -> CapabilitySpec:
        {body}
"""

Check = Callable[[str], list[str]]


@dataclass(frozen=True)
class Cap:
    """A synthetic capability: package ``name``, installed in distribution ``dist``."""

    name: str
    files: dict[str, str]
    requires: tuple[str, ...] = ()
    broken: bool = False
    dist: str = ""
    package: str = ""
    installed: bool = True
    callable_target: bool = False


def cap(
    name: str,
    *,
    files: dict[str, str] | None = None,
    requires: Sequence[str] = (),
    broken: bool = False,
    dist: str = "",
    package: str = "",
    installed: bool = True,
    callable_target: bool = False,
) -> Cap:
    """A capability; ``dist`` and ``package`` default to ``name``.

    ``callable_target`` discovers it through :func:`untaped.testing.provider_candidate`
    (a callable target) instead of a ``module:provider`` string.
    """
    return Cap(
        name,
        files or {},
        tuple(requires),
        broken,
        dist or name,
        package or name,
        installed,
        callable_target,
    )


Boundary = Callable[..., Check]


@pytest.fixture
def boundary(install: Install) -> Boundary:
    """``setup(*caps)`` installs them and returns ``check(name)``: the boundary lines.

    A capability with ``installed=False`` is only discovered, never importable.
    """

    def setup(*caps: Cap) -> Check:
        candidates: list[ProviderCandidate] = []
        for c in caps:
            body = 'raise RuntimeError("broken")' if c.broken else "return SPEC"
            init = dedent(_INIT).format(name=c.name, body=body)
            prefix = c.package.replace(".", "/")
            if c.installed:
                files = {f"{prefix}/{k}": v for k, v in c.files.items()}
                install({f"{prefix}/__init__.py": init, **files})
            if c.callable_target:
                spec = importlib.import_module(c.package).SPEC
                candidates.append(provider_candidate(spec, distribution=c.dist))
                continue
            candidates.append(
                ProviderCandidate(
                    distribution=c.dist,
                    name=c.name,
                    target=f"{c.package}:provider",
                    requires_dist=c.requires,
                )
            )

        def check(name: str) -> list[str]:
            found = capability_violations(name, candidates=candidates)
            return [line for line in found if "::import-boundary::" in line]

        return check

    return setup


def test_core_imports_other_than_the_sdk_are_violations(boundary: Boundary) -> None:
    check = boundary(cap("demo", files={"cli/__init__.py": "from untaped.cli import echo\n"}))
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::imports untaped.cli; use untaped.sdk"
    ]


def test_bare_untaped_and_import_statements_are_violations(boundary: Boundary) -> None:
    source = "import untaped\nimport untaped.settings as s\nfrom untaped import sdk, bootstrap\n"
    check = boundary(cap("demo", files={"cli/__init__.py": source}))
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::imports untaped; use untaped.sdk",
        "demo/cli/__init__.py:2::import-boundary::imports untaped.settings; use untaped.sdk",
        "demo/cli/__init__.py:3::import-boundary::imports untaped.bootstrap; use untaped.sdk",
    ]


def test_the_sdk_and_own_modules_are_allowed(boundary: Boundary) -> None:
    source = "from untaped.sdk import create_app\nfrom demo.domain import x\nimport json\n"
    check = boundary(
        cap("demo", files={"cli/__init__.py": source, "domain/__init__.py": "x = 1\n"})
    )
    assert check("demo") == []


def test_function_level_and_type_checking_imports_count(boundary: Boundary) -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from untaped.cli import a\n"
        "def f():\n"
        "    from untaped.settings import b\n"
    )
    check = boundary(cap("demo", files={"cli/__init__.py": source}))
    assert check("demo") == [
        "demo/cli/__init__.py:3::import-boundary::imports untaped.cli; use untaped.sdk",
        "demo/cli/__init__.py:5::import-boundary::imports untaped.settings; use untaped.sdk",
    ]


def test_a_waiver_suppresses_one_line(boundary: Boundary) -> None:
    source = "import untaped.cli  # untaped: allow import-boundary\nimport untaped.settings\n"
    check = boundary(cap("demo", files={"cli/__init__.py": source}))
    assert check("demo") == [
        "demo/cli/__init__.py:2::import-boundary::imports untaped.settings; use untaped.sdk"
    ]


def test_another_capability_only_through_its_api(boundary: Boundary) -> None:
    check = boundary(
        cap("other", files={"api.py": "x = 1\n", "domain/__init__.py": "y = 1\n"}),
        cap(
            "demo",
            files={"cli/__init__.py": "from other.api import x\nfrom other.domain import y\n"},
            requires=["other"],
        ),
    )
    assert check("demo") == [
        "demo/cli/__init__.py:2::import-boundary::imports other.domain; use other.api"
    ]


def test_importing_the_api_module_by_name_is_allowed(boundary: Boundary) -> None:
    check = boundary(
        cap("other", files={"api.py": "x = 1\n"}),
        cap("demo", files={"cli/__init__.py": "from other import api\n"}, requires=["other"]),
    )
    assert check("demo") == []


def test_a_requirement_under_a_platform_marker_is_declared(boundary: Boundary) -> None:
    check = boundary(
        cap("other", files={"api.py": "x = 1\n"}),
        cap(
            "demo",
            files={"cli/__init__.py": "from other.api import x\n"},
            requires=["other>=1; python_version >= '3' and sys_platform != 'nowhere'"],
        ),
    )
    assert check("demo") == []


def test_a_requirement_guarded_by_an_extra_is_not_declared(boundary: Boundary) -> None:
    check = boundary(
        cap("other", files={"api.py": "x = 1\n"}),
        cap(
            "demo",
            files={"cli/__init__.py": "from other.api import x\n"},
            requires=["other; extra == 'x'"],
        ),
    )
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::"
        "imports other.api but its distribution does not depend on other's"
    ]


def test_an_api_import_needs_a_declared_dependency(boundary: Boundary) -> None:
    check = boundary(
        cap("other", files={"api.py": "x = 1\n"}),
        cap("demo", files={"cli/__init__.py": "from other.api import x\n"}),
    )
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::"
        "imports other.api but its distribution does not depend on other's"
    ]


def test_capabilities_in_one_distribution_may_import_each_others_api(boundary: Boundary) -> None:
    check = boundary(
        cap("other", files={"api.py": "x = 1\n"}, dist="suite"),
        cap("demo", files={"cli/__init__.py": "from other.api import x\n"}, dist="suite"),
    )
    assert check("demo") == []


def test_a_quarantined_capability_still_counts_as_a_capability(boundary: Boundary) -> None:
    check = boundary(
        cap("other", files={"api.py": "x = 1\n", "domain/__init__.py": "y = 1\n"}, broken=True),
        cap("demo", files={"cli/__init__.py": "from other.domain import y\n"}, requires=["other"]),
    )
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::imports other.domain; use other.api"
    ]


def test_a_capability_nested_under_untaped_is_a_capability_not_core(boundary: Boundary) -> None:
    package = "untaped.capabilities.other"
    check = boundary(
        cap("other", package=package, installed=False),
        cap(
            "demo",
            files={"cli/__init__.py": f"def f():\n    from {package}.domain import y\n"},
            requires=["other"],
        ),
    )
    assert check("demo") == [
        f"demo/cli/__init__.py:2::import-boundary::imports {package}.domain; use {package}.api"
    ]


def test_a_capability_given_as_a_callable_candidate_is_a_capability(boundary: Boundary) -> None:
    check = boundary(
        cap("other", files={"domain/__init__.py": "y = 1\n"}, callable_target=True),
        cap("demo", files={"cli/__init__.py": "from other.domain import y\n"}, requires=["other"]),
    )
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::imports other.domain; use other.api"
    ]


def test_relative_imports_within_the_own_package_are_allowed(boundary: Boundary) -> None:
    check = boundary(
        cap(
            "demo",
            files={
                "cli/__init__.py": "from . import helpers\nfrom ..domain import x\n",
                "cli/helpers.py": "",
                "domain/__init__.py": "x = 1\n",
                "application/__init__.py": "from .ports import p\n",
                "application/ports.py": "p = 1\n",
            },
        )
    )
    assert check("demo") == []


def test_a_relative_import_into_a_sibling_capability_is_a_violation(boundary: Boundary) -> None:
    parent = "suite"
    check = boundary(
        cap("other", package=f"{parent}.other", files={"domain/__init__.py": "y = 1\n"}),
        cap(
            "demo",
            package=f"{parent}.demo",
            files={"cli/__init__.py": "from ...other.domain import y\n"},
            requires=["other"],
        ),
    )
    assert check("demo") == [
        f"demo/cli/__init__.py:1::import-boundary::imports {parent}.other.domain;"
        f" use {parent}.other.api"
    ]
