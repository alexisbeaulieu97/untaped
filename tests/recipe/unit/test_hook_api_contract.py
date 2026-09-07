"""Tests for the public hook authoring API contract."""

from __future__ import annotations

from untaped.capabilities.recipe.infrastructure import pack_scaffold


def test_public_hook_api_exposes_helper_types() -> None:
    from untaped.capabilities.recipe.hook_api import (
        HOOK_API_VERSION,
        HookHelpers,
        YamlDumpOptions,
        YamlIndentOptions,
    )

    indent: YamlIndentOptions = {"mapping": 2, "sequence": 4, "offset": 2}
    options: YamlDumpOptions = {"width": 120, "indent": indent}

    assert HOOK_API_VERSION == "0.10.0"
    assert options["indent"]["sequence"] == 4
    assert HookHelpers.__name__ == "HookHelpers"


def test_hook_api_versions_and_scaffold_floor_stay_in_sync() -> None:
    # The standalone release parity check (repo pyproject version) is retired
    # with the standalone distribution: the unified product versions as one
    # (4.0.0rc1) while PACKAGE_VERSION pins the external hook-contract floor.
    from untaped.capabilities.recipe._version import PACKAGE_VERSION
    from untaped.capabilities.recipe.hook_api import HOOK_API_VERSION

    package_major_minor = ".".join(PACKAGE_VERSION.split(".")[:2])
    contract_major_minor = ".".join(HOOK_API_VERSION.split(".")[:2])
    project_requirement, dev_requirement = pack_scaffold.hook_api_requirements(
        package_version=PACKAGE_VERSION,
        hook_api_version=HOOK_API_VERSION,
    )

    assert contract_major_minor
    assert package_major_minor
    assert project_requirement == ">=0.10,<1"
    assert dev_requirement == "untaped-recipe>=0.10"
    assert project_requirement == pack_scaffold._HOOK_API_PROJECT_REQUIREMENT
    assert dev_requirement == pack_scaffold._HOOK_API_DEV_REQUIREMENT


def test_hook_api_requirements_are_derived_from_versions() -> None:
    assert pack_scaffold.hook_api_requirements(
        package_version="2.4.0",
        hook_api_version="1.2.0",
    ) == (">=1.2,<2", "untaped-recipe>=1.2")
