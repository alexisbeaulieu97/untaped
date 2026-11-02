"""Registry-completeness contract for action commands.

:func:`~untaped_awx.commands.resources.resource_group` derives a command for each
``ActionSpec.name`` it knows (``launch``, ``sync``). An ``ActionSpec(name=...)`` it does not know
would ship in the catalog and never appear under the kind's group; this test
catches that at unit-test time.
"""

from __future__ import annotations

from untaped.runtime.declaration import target_of
from untaped.runtime.registry import resolve_target
from untaped.sdk import Group
from untaped_awx.commands.resources import RESOURCE_GROUPS
from untaped_awx.infrastructure.resources.specs import ALL_SPECS


def _group(kind: str) -> type[Group]:
    return next(group for group in RESOURCE_GROUPS if group.__doc__ == f"Manage {kind} resources.")


def test_every_kind_with_commands_has_its_group_in_the_catalog_s_order() -> None:
    kinds = [spec.kind for spec in ALL_SPECS if spec.commands]
    assert [group.__doc__ for group in RESOURCE_GROUPS] == [
        f"Manage {kind} resources." for kind in kinds
    ]


def test_every_actionspec_name_has_a_command() -> None:
    missing = [
        f"{spec.kind}.{action.name}"
        for spec in ALL_SPECS
        if spec.commands
        for action in spec.actions
        if action.name.title() not in {cls.__name__ for cls in _group(spec.kind).commands}
    ]
    assert not missing, (
        f"ActionSpec name(s) {missing!r} are declared on a kind but derive no command; "
        "add one in untaped_awx.commands.resources."
    )


def _commands(nodes: tuple[object, ...]) -> list[type]:
    found: list[type] = []
    for node in nodes:
        assert isinstance(node, type)
        found.append(node)
        if issubclass(node, Group):
            found += _commands(node.commands)
    return found


def test_every_resource_command_s_target_imports_back_to_it() -> None:
    for each in _commands(RESOURCE_GROUPS):
        assert resolve_target(target_of(each)) is each, target_of(each)
