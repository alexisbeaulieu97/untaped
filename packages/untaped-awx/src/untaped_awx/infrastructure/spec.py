"""Transport-aware extension of the domain :class:`ResourceSpec`.

:class:`AwxResourceSpec` adds the fields the framework needs to actually
talk to AWX (``api_path``) and to wire the CLI (``cli_name``,
``commands``, ``list_columns``). Use cases in ``application/`` depend
on :class:`ResourceSpec` (the domain type); concrete instances declared
here satisfy that interface structurally via inheritance.
"""

from __future__ import annotations

from untaped_awx.domain import CommandName, ResourceSpec


class AwxResourceSpec(ResourceSpec):
    """:class:`ResourceSpec` plus AWX REST + CLI wiring."""

    cli_name: str
    api_path: str
    list_columns: tuple[str, ...] = ()
    commands: tuple[CommandName, ...] = ("list", "get", "save", "apply")
