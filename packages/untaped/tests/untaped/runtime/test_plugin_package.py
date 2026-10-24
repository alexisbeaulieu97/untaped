"""Where a candidate's code is: its package, read from its entry point's target."""

from __future__ import annotations

import pytest

from untaped.runtime.plugin_package import candidate_package


@pytest.mark.parametrize(
    ("target", "package"),
    [
        ("untaped.core:SPEC", "untaped.core"),  # the module is the package
        ("untaped.core.plugin:Core", "untaped.core"),  # a class in a leaf module
        ("untaped_nowhere:SPEC", "untaped_nowhere"),  # not installed: the name as written
        ("no-colon", None),
    ],
)
def test_a_targets_package_is_its_module_or_the_package_holding_it(
    target: str, package: str | None
) -> None:
    assert candidate_package(target) == package
