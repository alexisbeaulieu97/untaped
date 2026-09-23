"""Tests for the public hook authoring API contract."""

from __future__ import annotations

from importlib.metadata import version

from packaging.version import Version

from untaped.capabilities.recipe.domain.hook_project import untaped_dev_requirement
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
    # The protocol floor remains tied to HOOK_API_VERSION while the editor/type
    # discovery dependency follows the unified product release independently.
    from untaped.capabilities.recipe.hook_api import HOOK_API_VERSION

    contract_major_minor = ".".join(HOOK_API_VERSION.split(".")[:2])
    project_requirement, dev_requirement = pack_scaffold.hook_api_requirements(
        hook_api_version=HOOK_API_VERSION
    )

    assert contract_major_minor
    assert project_requirement == ">=0.10,<1"
    assert dev_requirement == _expected_dev_requirement()
    assert project_requirement == pack_scaffold._HOOK_API_PROJECT_REQUIREMENT
    assert dev_requirement == pack_scaffold._HOOK_API_DEV_REQUIREMENT


def test_hook_api_requirements_are_derived_from_versions() -> None:
    assert pack_scaffold.hook_api_requirements(
        hook_api_version="1.2.0",
    ) == (">=1.2,<2", _expected_dev_requirement())


def _expected_dev_requirement() -> str:
    installed = Version(version("untaped"))
    return f"untaped>={installed.public},<{installed.major + 1}"


def test_untaped_dev_requirement_is_bounded_to_the_installed_major() -> None:
    assert untaped_dev_requirement("6.0.1") == "untaped>=6.0.1,<7"
    assert untaped_dev_requirement("7.2.0.dev3+g1234") == "untaped>=7.2.0.dev3,<8"
