"""The records ``untaped hello`` writes.

A record's kind comes from the command that prints it: ``Say`` prints
:class:`Salutation`, so its kind is ``hello.salutation``.
"""

from __future__ import annotations

from typing import Annotated

from untaped.sdk import Column, Key, Record


class Salutation(Record):
    """One greeting ``untaped hello say`` made: whom it greets and what it says."""

    name: Annotated[str, Key, Column]
    text: Annotated[str, Column]
