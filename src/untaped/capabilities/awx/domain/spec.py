"""Declarative description of a single AWX resource kind.

A :class:`ResourceSpec` is the **domain** view of a kind: identity,
foreign-key references, secret paths, fidelity, available actions. It
carries no transport detail (URL paths, HTTP verbs, CLI names) — those
live in :class:`untaped.capabilities.awx.infrastructure.spec.AwxResourceSpec`, which
extends ``ResourceSpec`` with the additional fields the framework needs
to actually talk to AWX and wire CLI commands.

Use cases in ``application/`` annotate ``ResourceSpec`` so they can be
exercised against any kind without depending on AWX-specific transport.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

Fidelity = Literal["full", "partial", "read_only"]
"""Restore-fidelity tier per kind, surfaced on save."""

CommandName = Literal[
    "list",
    "get",
    "save",
    "apply",
    "patch",
    "edit",
    "launch",
    "sync",
    "delete",
]
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

    ordered: bool = False
    """Whether this sub-endpoint relationship is order-sensitive.

    Most AWX relationships are sets.  Ordered many-to-many fields retain the
    server's returned order and therefore need exact sequence comparison and
    an explicit reordering operation during reconciliation.
    """

    polymorphic: bool = False
    kind_in_value: str | None = None
    """For polymorphic FKs, the key inside the value that holds the kind."""

    scope_field_in_value: str | None = None
    """For polymorphic FKs, the key inside the value that holds the scope."""

    @model_validator(mode="after")
    def _kind_required_unless_polymorphic(self) -> FkRef:
        """A non-polymorphic FK must name its ``kind``.

        Makes ``ApplyPlanner.plan_payload``'s ``assert ref.kind is not None``
        correct by construction: only polymorphic refs (resolved out-of-band,
        never reaching that assert) may omit ``kind``.
        """
        if self.kind is None and not self.polymorphic:
            raise ValueError("FkRef.kind is required unless the FK is polymorphic")
        return self


class ActionSpec(BaseModel):
    """A custom POST/PATCH action a kind exposes (e.g. ``launch``, ``sync``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: str | None
    """Direct action endpoint; None requires fixed-target expansion first."""

    method: Literal["POST", "PATCH"] = "POST"
    returns: Literal["job", "workflow_job", "project_update", "inventory_update", "none"] = "none"
    accepts: frozenset[str] = frozenset()
    """Optional payload fields the CLI factory exposes as flags."""


class ResourceSpec(BaseModel):
    """Per-kind domain configuration consumed by application use cases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    identity_keys: tuple[str, ...]
    canonical_fields: tuple[str, ...]
    """Fields the tool curates for a kind.

    For ``save`` this is a strict export allowlist — only these are written to
    the saved YAML, keeping it portable. For ``apply`` it is **advisory**: the
    payload is passthrough (see ``ApplyPlanner.plan_payload``) and
    ``canonical_fields`` only feeds :attr:`known_fields` (which decides whether
    an apply field is warned about as unrecognized) — it is no longer a write
    gate.
    """
    read_only_fields: tuple[str, ...] = ()
    fk_refs: tuple[FkRef, ...] = ()
    launch_fk_refs: tuple[FkRef, ...] = ()
    """Foreign keys exposed only on the ``launch`` action payload.

    Distinct from ``fk_refs`` because the launch endpoint accepts FK
    overrides that aren't fields of the resource itself (e.g. ``labels``,
    ``instance_groups``). Used by the test runner's name resolver.
    """
    secret_paths: tuple[str, ...] = ()
    structured_text_fields: tuple[str, ...] = ()
    """Explicit YAML/JSON mapping fields encoded as text at the HTTP boundary."""
    server_enriched_fields: tuple[str, ...] = ()
    """Top-level fields AWX may enrich while accepting a user replacement.

    These are narrow, explicit exceptions to the exact replacement contract;
    an omitted field or extra server key remains a verification failure for
    every other user-owned map and sequence.
    """
    actions: tuple[ActionSpec, ...] = ()
    singleton_parent: bool = False
    """Prepared resource has one managed target per parent, regardless of its name."""
    apply_strategy: str = "default"
    """Behavior selector: which write path the apply pipeline dispatches to.
    The string is opaque to the domain — :class:`StrategyResolver` (an
    infrastructure adapter) maps it to a concrete :class:`ApplyStrategy`."""
    fidelity: Fidelity = "full"
    fidelity_note: str | None = None

    @property
    def known_fields(self) -> frozenset[str]:
        """Every field this spec has metadata for.

        The union of ``canonical_fields``, ``identity_keys``, the ``fk_refs``
        field names, and ``read_only_fields`` — the single source of truth for
        "does this tool recognize this field?" Consumed by the apply
        unrecognized-field warning, bulk-save filter validation, and the
        catalog list-column invariant.
        """
        return (
            frozenset(self.canonical_fields)
            | frozenset(self.identity_keys)
            | {ref.field for ref in self.fk_refs}
            | frozenset(self.read_only_fields)
        )
