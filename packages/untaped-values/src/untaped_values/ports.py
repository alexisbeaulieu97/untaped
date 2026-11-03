"""The protocol a consumer fills: how a missing value is asked."""

from __future__ import annotations

from typing import Protocol

from untaped.sdk import experimental
from untaped_values.inputs import Input


@experimental
class Ask(Protocol):
    """How a missing value is asked: the answer, before conversion to the input's type.

    ``label`` says what the value is for, such as a target; empty when the
    value is for the whole run.
    """

    def __call__(self, name: str, spec: Input, *, label: str) -> object: ...
