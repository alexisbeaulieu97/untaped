"""Registry-completeness contract for action commands.

`make_resource_app` dispatches `ActionSpec.name` through the
`ACTION_BUILDERS` dict in `cli/_factory.py`. Unknown names fall
through silently — same behaviour as the prior if/elif chain. That
silent fallthrough is fine UNTIL someone adds an `ActionSpec(name=…)`
without registering a builder; then the spec ships, the catalog
exports it, and the action just doesn't appear under the kind's CLI.

This test catches that regression at unit-test time.
"""

from __future__ import annotations

from untaped_awx.cli._factory import ACTION_BUILDERS
from untaped_awx.infrastructure.specs import ALL_SPECS


def test_every_actionspec_name_has_a_builder() -> None:
    declared = {action.name for spec in ALL_SPECS for action in spec.actions}
    missing = declared - ACTION_BUILDERS.keys()
    assert not missing, (
        f"ActionSpec name(s) {sorted(missing)!r} are declared on a kind "
        "but have no builder registered in `ACTION_BUILDERS`. Add the "
        "builder + registry entry in cli/_factory.py."
    )
