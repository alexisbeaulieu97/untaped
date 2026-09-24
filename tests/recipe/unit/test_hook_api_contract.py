"""Tests for the public hook authoring API contract."""

from __future__ import annotations

from importlib.metadata import version

from packaging.version import Version

from untaped.capabilities.recipe.domain.hook_project import untaped_dev_requirement
from untaped.capabilities.recipe.hook_api import HOOK_API_VERSION
from untaped.capabilities.recipe.infrastructure import pack_scaffold


def test_hook_api_requirements_are_derived_from_versions() -> None:
    # The protocol floor follows HOOK_API_VERSION while the editor/type
    # discovery dependency follows the unified product release independently.
    installed = Version(version("untaped"))
    dev_requirement = f"untaped>={installed.public},<{installed.major + 1}"

    assert HOOK_API_VERSION == "0.10.0"
    assert pack_scaffold.hook_api_requirements() == (">=0.10,<1", dev_requirement)
    assert pack_scaffold.hook_api_requirements(hook_api_version="1.2.0") == (
        ">=1.2,<2",
        dev_requirement,
    )


def test_untaped_dev_requirement_is_bounded_to_the_installed_major() -> None:
    assert untaped_dev_requirement("6.0.1") == "untaped>=6.0.1,<7"
    assert untaped_dev_requirement("7.2.0.dev3+g1234") == "untaped>=7.2.0.dev3,<8"
