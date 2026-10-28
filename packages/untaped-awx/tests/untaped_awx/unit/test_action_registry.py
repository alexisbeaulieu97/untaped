"""Registry-completeness contract for action commands.

:func:`resource_group` derives a command for each ``ActionSpec.name`` it
knows (``launch``, ``sync``). An ``ActionSpec(name=...)`` it does not know
would ship in the catalog and never appear under the kind's group; this test
catches that at unit-test time.
"""

from __future__ import annotations

from untaped_awx.commands.resources import resource_group
from untaped_awx.infrastructure.resources.specs import ALL_SPECS


def test_every_actionspec_name_has_a_command() -> None:
    missing = [
        f"{spec.kind}.{action.name}"
        for spec in ALL_SPECS
        if spec.commands
        for action in spec.actions
        if action.name.title() not in {cls.__name__ for cls in resource_group(spec).commands}
    ]
    assert not missing, (
        f"ActionSpec name(s) {missing!r} are declared on a kind but derive no command; "
        "add one in untaped_awx.commands.resources."
    )
