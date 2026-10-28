"""What every awx command shares: the controller it talks to, and the kind it works on."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import ClassVar

from untaped.sdk import Command
from untaped_awx.commands.context import AwxContext
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.settings import AwxSettings


class AwxCommand(Command[AwxSettings]):
    """A command that talks to AWX through the profile's ``awx`` settings."""

    @contextmanager
    def awx(self) -> Iterator[AwxContext]:
        """The controller's client and its use cases for this run, closed after the block."""
        context = AwxContext(self)
        try:
            yield context
        finally:
            context.close()


class ResourceCommand(AwxCommand):
    """A command of one kind's group (``job-templates list``): ``spec`` is that kind.

    Each kind's group derives its commands from these classes
    (:mod:`untaped_awx.commands.resources`), setting ``spec`` and replacing the
    fields whose type depends on the kind: its scope and the records ``--stdin``
    reads.
    """

    spec: ClassVar[AwxResourceSpec]


__all__ = ["AwxCommand", "ResourceCommand"]
