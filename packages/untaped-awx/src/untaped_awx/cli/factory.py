"""Factory for per-resource Cyclopts sub-apps.

``make_resource_app(spec)`` builds the Cyclopts sub-app for a single
``ResourceSpec`` by dispatching the spec's ``commands`` tuple and
``actions`` list to per-command builders defined in sibling modules
(``_list.py``, ``_get.py``, …). The ``ACTION_BUILDERS`` registry maps
``ActionSpec.name`` to its builder so new custom actions plug in
without editing the factory body.
"""

from __future__ import annotations

from collections.abc import Callable

from cyclopts import App

from untaped.sdk import create_app
from untaped_awx.cli._copy import _add_copy
from untaped_awx.cli._delete import _add_delete
from untaped_awx.cli._edit import _add_edit
from untaped_awx.cli._get import _add_get
from untaped_awx.cli._list import _add_list
from untaped_awx.cli._patch import _add_patch
from untaped_awx.cli._rename import _add_rename
from untaped_awx.cli._save import _add_save
from untaped_awx.cli._sync import _add_sync
from untaped_awx.cli.launch import _add_launch
from untaped_awx.cli.membership_commands import register_membership_subapp
from untaped_awx.cli.options import scope_parameter
from untaped_awx.infrastructure.spec import AwxResourceSpec


def make_resource_app(spec: AwxResourceSpec) -> App:
    """Build the Cyclopts sub-app for a single kind based on ``spec.commands``."""
    app = create_app(
        name=spec.cli_name,
        help=f"Manage {spec.kind} resources.",
    )
    app.default_parameter = scope_parameter(spec)

    if "list" in spec.commands:
        _add_list(app, spec)
    if "get" in spec.commands:
        _add_get(app, spec)
    if "save" in spec.commands:
        _add_save(app, spec)
    if "apply" in spec.commands:
        # Declarative apply is the root ``awx apply``; kinds keep patch/edit.
        _add_patch(app, spec)
        _add_edit(app, spec)
    if "delete" in spec.commands:
        _add_delete(app, spec)
    if "copy" in spec.commands:
        _add_copy(app, spec)
    if "rename" in spec.commands:
        _add_rename(app, spec)
    for action in spec.actions:
        builder = ACTION_BUILDERS.get(action.name)
        if builder is not None:
            builder(app, spec)
    for ref in spec.fk_refs:
        if ref.multi and ref.sub_endpoint:
            register_membership_subapp(app, spec, ref)

    return app


# Maps an :class:`ActionSpec.name` to the builder that wires its CLI
# command. Adding a new custom action means: (1) declare its
# :class:`ActionSpec` on the per-kind spec, (2) implement an
# ``_add_<action>(app, spec)`` builder in its own sibling module, and
# (3) register it here. :func:`make_resource_app` itself stays
# untouched as new actions are added.
ACTION_BUILDERS: dict[str, Callable[[App, AwxResourceSpec], None]] = {
    "launch": _add_launch,
    "sync": _add_sync,
}


__all__ = ["ACTION_BUILDERS", "make_resource_app"]
