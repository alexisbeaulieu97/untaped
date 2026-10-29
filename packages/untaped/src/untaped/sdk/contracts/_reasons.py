"""Why a provider's answer, offer or ranking is what it is: one table of words, typed by use.

:class:`Reason` holds every word once; each use is typed by its subset
(:data:`SkipReason` on ``Skipped.reason``, :data:`QuarantineReason` on a
quarantined offer, :data:`DoctorReason` on a doctor row), so a type checker
says which reasons an answer can carry. The values are the words on the wire
(``doctor -f json``, ``plugin list --contracts``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal


class Reason(StrEnum):
    """A stable reason word; ``str(Reason.not_configured)`` is ``not-configured``."""

    not_configured = "not-configured"
    """The provider's ``ready()`` said no: its settings are missing or don't validate."""
    no_cache = "no-cache"
    """Asked for a cached answer only (``--cached``), and none is stored."""
    invalid_item = "invalid-item"
    """The answer doesn't validate as the owner's model."""
    unbridged_item = "unbridged-item"
    """An issued item without its provider's source, or naming another plugin."""
    raised = "raised"
    """The provider raised."""
    owner_out_of_range = "owner-out-of-range"
    duplicate_kind = "duplicate-kind"
    unresolved_item_type = "unresolved-item-type"
    missing_bridge = "missing-bridge"
    unserialisable_signature = "unserialisable-signature"
    bad_provider = "bad-provider"
    unused_method = "unused-method"
    """A provider fills a method its contract no longer has."""
    rank_unknown_method = "rank-unknown-method"
    rank_not_installed = "rank-not-installed"
    active = "active"
    """A doctor row's title for a provider that answers; goes with the 11.x reader."""
    bad_contracts = "bad-contracts"
    """An 11.x owner whose contracts can't be read; goes with the 11.x owner reader."""


#: Why the registry set a provider's offer aside at composition.
QuarantineReason = Literal[
    Reason.owner_out_of_range,
    Reason.duplicate_kind,
    Reason.unresolved_item_type,
    Reason.missing_bridge,
    Reason.unserialisable_signature,
    Reason.bad_provider,
]

#: Why a provider was not asked, or why its answer doesn't count.
SkipReason = (
    Literal[
        Reason.not_configured,
        Reason.no_cache,
        Reason.invalid_item,
        Reason.unbridged_item,
    ]
    | QuarantineReason
)

#: Why an asked provider's answer is an error: it raised, or the SDK failed it.
ErrorReason = Literal[Reason.raised, Reason.invalid_item, Reason.unbridged_item] | QuarantineReason

#: The title of a doctor row about contracts.
DoctorReason = (
    Literal[
        Reason.active,
        Reason.not_configured,
        Reason.unused_method,
        Reason.bad_contracts,
        Reason.rank_unknown_method,
        Reason.rank_not_installed,
    ]
    | QuarantineReason
)
