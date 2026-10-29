"""The fake records lint's tests read, and the plugin that gives them their kinds.

They live apart from the fake providers: a record takes its kind from the
plugin owning its module, so ``linttest.widget`` stays the same whichever
fake providers a test composes.
"""

from __future__ import annotations

from pydantic import BaseModel

from untaped.sdk import Plugin, Record


class Widget(Record):
    """A record two of acme's rules read."""

    name: str


class Gadget(Record):
    """A record one of gizmo's rules reads."""

    name: str


class Sprocket(Record):
    """A record no rule reads."""

    name: str


class _NoSettings(BaseModel):
    """Linttest configures nothing."""


class Linttest(Plugin):
    """The fake records' plugin, which gives them their kinds (``linttest.widget``)."""

    settings = _NoSettings
    records = (Widget, Gadget, Sprocket)
