"""Contracts: interfaces one plugin declares and others fill (``untaped.contracts``).

An owner declares a contract as an ABC subclass of :class:`Contract`, marks it
``@experimental``, lists it in its ``PluginSpec.contracts`` and asks it with
:func:`gather` (and :func:`select_one` or :func:`convert`). A provider fills it
by subclassing it, optionally with its own record type ``T`` and the owner's
``@bridge`` method, and lists an instance in ``PluginSpec.provides[owner]``.

This module root is never imported by :mod:`untaped.sdk`, so a command that
asks no contract pays nothing for it. The rules are in ``docs/contracts.md``.
"""

from __future__ import annotations

from untaped.contracts._declare import (
    Configured,
    Contract,
    Issued,
    NotReady,
    Source,
    bridge,
    cached,
    listing,
)
from untaped.contracts._gather import Answer, Answers, Failed, Ok, Skipped, gather
from untaped.contracts._select import Ambiguous, NotFound, convert, select_one
from untaped.records import Record

__all__ = [
    "Ambiguous",
    "Answer",
    "Answers",
    "Configured",
    "Contract",
    "Failed",
    "Issued",
    "NotFound",
    "NotReady",
    "Ok",
    "Record",
    "Skipped",
    "Source",
    "bridge",
    "cached",
    "convert",
    "gather",
    "listing",
    "select_one",
]
