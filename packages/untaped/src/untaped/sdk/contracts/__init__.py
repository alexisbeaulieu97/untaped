"""Contracts: interfaces one plugin declares and others fill (``untaped.sdk.contracts``).

An owner declares a contract as an ABC subclass of :class:`Contract`, marks it
``@experimental``, lists it in its plugin class's ``offers`` and asks it with
:func:`gather` (and :func:`select_one`, or ``Stdin(via=...)``). A provider fills it
by subclassing it and :class:`Provider`, optionally with its own record type
``T`` and the contract's bridge (the method taking ``T`` and returning the
owner's model), and its plugin class lists it in ``fills``.

This module root is never imported by :mod:`untaped.sdk`, so a command that
asks no contract pays nothing for it. The rules are in ``docs/contracts.md``.
"""

from __future__ import annotations

from untaped.sdk.contracts._declare import (
    Contract,
    NotReady,
    Provider,
    cached,
)
from untaped.sdk.contracts._gather import (
    Answers,
    Errored,
    NoProviderReady,
    Ok,
    Skipped,
    gather,
)
from untaped.sdk.contracts._providing import providing
from untaped.sdk.contracts._reasons import Reason
from untaped.sdk.contracts._select import Ambiguous, NotFound, select_one
from untaped.sdk.records import Issued, Record, Source

__all__ = [
    "Ambiguous",
    "Answers",
    "Contract",
    "Errored",
    "Issued",
    "NoProviderReady",
    "NotFound",
    "NotReady",
    "Ok",
    "Provider",
    "Reason",
    "Record",
    "Skipped",
    "Source",
    "cached",
    "gather",
    "providing",
    "select_one",
]
