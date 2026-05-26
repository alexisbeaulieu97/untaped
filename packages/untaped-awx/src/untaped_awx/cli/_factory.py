"""Factory for per-resource Typer sub-apps.

``make_resource_app(spec)`` builds the Typer sub-app for a single
``ResourceSpec`` by dispatching the spec's ``commands`` tuple and
``actions`` list to per-command builders defined in sibling modules
(``_list.py``, ``_get.py``, …). The ``ACTION_BUILDERS`` registry maps
``ActionSpec.name`` to its builder so new custom actions plug in
without editing the factory body.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from untaped_awx.cli._apply import _add_apply
from untaped_awx.cli._delete import _add_delete
from untaped_awx.cli._get import _add_get
from untaped_awx.cli._launch import _add_launch
from untaped_awx.cli._list import _add_list
from untaped_awx.cli._save import _add_save
from untaped_awx.cli._update import _add_update
from untaped_awx.cli.membership_commands import register_membership_subapp
from untaped_awx.infrastructure.spec import AwxResourceSpec


def make_resource_app(spec: AwxResourceSpec) -> typer.Typer:
    """Build the Typer sub-app for a single kind based on ``spec.commands``."""
    app = typer.Typer(
        name=spec.cli_name,
        help=f"Manage {spec.kind} resources.",
        no_args_is_help=True,
    )

    @app.callback()
    def _callback() -> None:
        """Sub-app dispatcher."""

    if "list" in spec.commands:
        _add_list(app, spec)
    if "get" in spec.commands:
        _add_get(app, spec)
    if "save" in spec.commands:
        _add_save(app, spec)
    if "apply" in spec.commands:
        _add_apply(app, spec)
    if "delete" in spec.commands:
        _add_delete(app, spec)
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
ACTION_BUILDERS: dict[str, Callable[[typer.Typer, AwxResourceSpec], None]] = {
    "launch": _add_launch,
    "update": _add_update,
}


__all__ = ["ACTION_BUILDERS", "make_resource_app"]
