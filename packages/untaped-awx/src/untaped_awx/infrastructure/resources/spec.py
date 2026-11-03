"""The CLI wiring of the domain :class:`ResourceSpec`.

:class:`AwxResourceSpec` adds the fields the commands need (``cli_name``,
``commands``, ``list_columns``). Where a kind's records live on the controller
is the adapter's to know (:mod:`untaped_awx.infrastructure.controller`). Use
cases in ``application/`` depend on :class:`ResourceSpec`; the catalog's specs
satisfy it by inheritance.
"""

from __future__ import annotations

from untaped.sdk import SettingKey
from untaped_awx.domain import CommandName, ResourceSpec


class AwxResourceSpec(ResourceSpec):
    """:class:`ResourceSpec` plus its CLI wiring."""

    cli_name: str
    #: A list's default columns: record fields, and keys AWX sends that it does not declare.
    list_columns: tuple[SettingKey | str, ...] = ()
    commands: tuple[CommandName, ...] = ("list", "get", "save", "apply")
