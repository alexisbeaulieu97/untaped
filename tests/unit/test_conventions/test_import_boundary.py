"""Import boundary: capability code reaches core only through ``untaped.sdk``.

Capabilities are synthetic packages written into a temporary site and
discovered through explicit candidates, so each case controls the
distributions and their ``Requires-Dist``.
"""

from __future__ import annotations

from collections.abc import Callable
from textwrap import dedent

import pytest

from test_conventions.support import Install
from untaped.capabilities.registry import ProviderCandidate
from untaped.conventions import capability_violations

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

Boundary = Callable[[str], list[str]]


@pytest.fixture
def boundary(install: Install) -> Callable[..., Boundary]:
    """``setup(*capabilities)`` -> ``check(name)`` listing that capability's boundary lines.

    A capability is ``(name, files, requires, broken)``; its package and
    distribution are both ``name``.
    """

    def setup(
        *capabilities: tuple[str, dict[str, str], list[str], bool],
    ) -> Boundary:
        candidates: list[ProviderCandidate] = []
        for name, files, requires, broken in capabilities:
            body = 'raise RuntimeError("broken")' if broken else "return SPEC"
            init = dedent(_INIT).format(name=name, body=body)
            install({f"{name}/__init__.py": init, **{f"{name}/{k}": v for k, v in files.items()}})
            candidates.append(
                ProviderCandidate(
                    distribution=name,
                    name=name,
                    target=f"{name}:provider",
                    requires_dist=tuple(requires),
                )
            )

        def check(name: str) -> list[str]:
            found = capability_violations(name, candidates=candidates)
            return [line for line in found if "::import-boundary::" in line]

        return check

    return setup


def test_core_imports_other_than_the_sdk_are_violations(boundary) -> None:  # type: ignore[no-untyped-def]
    check = boundary(("demo", {"cli/__init__.py": "from untaped.cli import echo\n"}, [], False))
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::imports untaped.cli; use untaped.sdk"
    ]


def test_bare_untaped_and_import_statements_are_violations(boundary) -> None:  # type: ignore[no-untyped-def]
    source = "import untaped\nimport untaped.settings as s\nfrom untaped import sdk, bootstrap\n"
    check = boundary(("demo", {"cli/__init__.py": source}, [], False))
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::imports untaped; use untaped.sdk",
        "demo/cli/__init__.py:2::import-boundary::imports untaped.settings; use untaped.sdk",
        "demo/cli/__init__.py:3::import-boundary::imports untaped.bootstrap; use untaped.sdk",
    ]


def test_the_sdk_and_own_modules_are_allowed(boundary) -> None:  # type: ignore[no-untyped-def]
    source = "from untaped.sdk import create_app\nfrom demo.domain import x\nimport json\n"
    check = boundary(
        ("demo", {"cli/__init__.py": source, "domain/__init__.py": "x = 1\n"}, [], False)
    )
    assert check("demo") == []


def test_function_level_and_type_checking_imports_count(boundary) -> None:  # type: ignore[no-untyped-def]
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from untaped.cli import a\n"
        "def f():\n"
        "    from untaped.settings import b\n"
    )
    check = boundary(("demo", {"cli/__init__.py": source}, [], False))
    assert check("demo") == [
        "demo/cli/__init__.py:3::import-boundary::imports untaped.cli; use untaped.sdk",
        "demo/cli/__init__.py:5::import-boundary::imports untaped.settings; use untaped.sdk",
    ]


def test_a_waiver_suppresses_one_line(boundary) -> None:  # type: ignore[no-untyped-def]
    source = "import untaped.cli  # untaped: allow import-boundary\nimport untaped.settings\n"
    check = boundary(("demo", {"cli/__init__.py": source}, [], False))
    assert check("demo") == [
        "demo/cli/__init__.py:2::import-boundary::imports untaped.settings; use untaped.sdk"
    ]


def test_another_capability_only_through_its_api(boundary) -> None:  # type: ignore[no-untyped-def]
    check = boundary(
        ("other", {"api.py": "x = 1\n", "domain/__init__.py": "y = 1\n"}, [], False),
        (
            "demo",
            {"cli/__init__.py": "from other.api import x\nfrom other.domain import y\n"},
            ["other"],
            False,
        ),
    )
    assert check("demo") == [
        "demo/cli/__init__.py:2::import-boundary::imports other.domain; use other.api"
    ]


def test_importing_the_api_module_by_name_is_allowed(boundary) -> None:  # type: ignore[no-untyped-def]
    check = boundary(
        ("other", {"api.py": "x = 1\n"}, [], False),
        ("demo", {"cli/__init__.py": "from other import api\n"}, ["other>=1; extra == 'x'"], False),
    )
    assert check("demo") == []


def test_an_api_import_needs_a_declared_dependency(boundary) -> None:  # type: ignore[no-untyped-def]
    check = boundary(
        ("other", {"api.py": "x = 1\n"}, [], False),
        ("demo", {"cli/__init__.py": "from other.api import x\n"}, [], False),
    )
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::"
        "imports other.api but its distribution does not depend on other's"
    ]


def test_a_quarantined_capability_still_counts_as_a_capability(boundary) -> None:  # type: ignore[no-untyped-def]
    check = boundary(
        ("other", {"api.py": "x = 1\n", "domain/__init__.py": "y = 1\n"}, [], True),
        ("demo", {"cli/__init__.py": "from other.domain import y\n"}, ["other"], False),
    )
    assert check("demo") == [
        "demo/cli/__init__.py:1::import-boundary::imports other.domain; use other.api"
    ]


def test_the_repo_capabilities_pass_without_waivers() -> None:
    for name in ("ansible", "awx", "github", "jira", "recipe", "workspace"):
        found = capability_violations(name)
        assert [line for line in found if "::import-boundary::" in line] == []
