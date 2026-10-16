"""The records ``untaped hello`` writes, each declaring its kind on the model.

The app imports this module, so ``untaped plugin schema hello.salutation``
finds the kind before any command runs. Everything else names the model:
``kind_of(Salutation)``, ``accepts=(Salutation,)``.
"""

from __future__ import annotations

from untaped.sdk import Record


class Salutation(Record, kind="hello.salutation"):
    """One greeting ``untaped hello say`` made: whom it greets and what it says."""

    name: str
    text: str
