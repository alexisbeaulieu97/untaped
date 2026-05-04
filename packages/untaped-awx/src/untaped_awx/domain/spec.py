"""Declarative description of a single AWX resource kind.

A :class:`ResourceSpec` is the **domain** view of a kind: identity,
foreign-key references, secret paths, fidelity, available actions. It
carries no transport detail (URL paths, HTTP verbs, CLI names) — those
live in :class:`untaped_awx.infrastructure.spec.AwxResourceSpec`, which
extends ``ResourceSpec`` with the additional fields the framework needs
to actually talk to AWX and wire CLI commands.

Use cases in ``application/`` annotate ``ResourceSpec`` so they can be
exercised against any kind without depending on AWX-specific transport.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Fidelity = Literal["full", "partial", "read_only"]
"""Restore-fidelity tier per kind, surfaced on save."""

CommandName = Literal["list", "get", "save", "apply", "launch", "update"]
"""Commands the CLI factory may wire for a kind."""


class FkRef(BaseModel):
    """Foreign-key declaration for a field in a resource spec.

    For most resources, a single ``kind`` is fixed and resolution is
    scoped (e.g. ``project`` lives within ``organization``). Schedule's
    ``parent`` is *polymorphic* — its value carries the kind inline.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    kind: str | None = None
    """The referenced kind. ``None`` only for polymorphic FKs."""

    scope_field: str | None = None
    """Name of a sibling field whose value scopes the FK (e.g. ``organization``)."""

    multi: bool = False
    """True for list-of-references fields (e.g. ``credentials`` on JobTemplate)."""

    sub_endpoint: str | None = None
    """Multi-FK exposed via a separate sub-endpoint (e.g. ``credentials/``)."""

    polymorphic: bool = False
    kind_in_value: str | None = None
    """For polymorphic FKs, the key inside the value that holds the kind."""

    scope_field_in_value: str | None = None
    """For polymorphic FKs, the key inside the value that holds the scope."""


class ActionSpec(BaseModel):
    """A custom POST/PATCH action a kind exposes (e.g. ``launch``, ``update``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: str
    method: Literal["POST", "PATCH"] = "POST"
    returns: Literal["job", "none"] = "none"
    accepts: frozenset[str] = frozenset()
    """Optional payload fields the CLI factory exposes as flags."""


class ResourceSpec(BaseModel):
    """Per-kind domain configuration consumed by application use cases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    identity_keys: tuple[str, ...]
    canonical_fields: tuple[str, ...]
    read_only_fields: tuple[str, ...] = ()
    fk_refs: tuple[FkRef, ...] = ()
    launch_fk_refs: tuple[FkRef, ...] = ()
    """Foreign keys exposed only on the ``launch`` action payload.

    Distinct from ``fk_refs`` because the launch endpoint accepts FK
    overrides that aren't fields of the resource itself (e.g. ``labels``,
    ``instance_groups``). Used by the test runner's name resolver.
    """
    secret_paths: tuple[str, ...] = ()
    actions: tuple[ActionSpec, ...] = ()
    apply_strategy: str = "default"
    """Behavior selector: which write path the apply pipeline dispatches to.
    The string is opaque to the domain — :class:`StrategyResolver` (an
    infrastructure adapter) maps it to a concrete :class:`ApplyStrategy`."""
    fidelity: Fidelity = "full"
    fidelity_note: str | None = None
