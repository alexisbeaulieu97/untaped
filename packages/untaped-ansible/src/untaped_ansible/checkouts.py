"""What checks out a repository at a ref: ``ansible.checkout`` and the ``CheckoutSource`` contract.

ansible never knows who runs its dependents: every installed plugin that
fills :class:`CheckoutSource` (with its job templates, say) answers, and
``ansible impact checkouts ROLE`` hangs each checkout under the dependent it
checks out.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import timedelta

from pydantic import Field

from untaped.sdk import Issued, Record, experimental
from untaped.sdk.contracts import Contract, cached
from untaped_git.api import GitUrl


@experimental
class Checkout(Issued):
    """Something that checks out a repository at a ref; ``source`` names who issued it."""

    name: str = Field(description="What checks it out, as its provider names it.")
    url: GitUrl = Field(description="The repository it checks out.")
    ref: str | None = Field(
        default=None, description="The ref it checks out; null for the repository's default branch."
    )
    link: str | None = Field(default=None, description="Its page at its provider, if it has one.")
    via: str | None = Field(
        default=None, description="Through what it checks the repository out (a project, say)."
    )


@experimental
class CheckoutSource[T: Record = Checkout](Contract):
    """What checks out a repository at a ref: job templates, say.

    ``T`` is the provider's own record type; it defaults to Checkout.
    """

    def to_checkout(self, item: T) -> Checkout:
        """Turn the provider's own record into a Checkout (only when ``T`` is not Checkout)."""
        raise NotImplementedError

    @cached(max_age=timedelta(hours=1), label="checkouts")
    @abstractmethod
    def checkouts(self) -> list[Checkout]:
        """Every checkout the provider knows of in the active profile."""


__all__ = ["Checkout", "CheckoutSource"]
