"""Prepare fixed body writes and verify their returned controller state.

Secret preservation, field diffs, and convergence are owned here. Adapters
must prepare once and execute that payload without another name lookup.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from untaped.capabilities.awx.application._secret_paths import strip_encrypted_in_place
from untaped.capabilities.awx.application.apply_field_diff import PRESERVED_SECRET_NOTE, FieldDiff
from untaped.capabilities.awx.application.apply_secret_policy import SecretPreservationPolicy
from untaped.capabilities.awx.application.apply_verifier import ApplyVerifier
from untaped.capabilities.awx.application.ports import RawHttpResourceClient
from untaped.capabilities.awx.domain import FieldChange, Resource, ResourceSpec
from untaped.capabilities.awx.errors import AwxApiError, BadRequest


@dataclass(frozen=True)
class PreparedBody:
    """Execution values are deliberately absent from the plan repr."""

    payload: dict[str, Any] = field(repr=False)
    changes: tuple[FieldChange, ...] = field(repr=False)
    preserved: tuple[str, ...]
    dropped_undeclared: tuple[str, ...]


class BodyOperations:
    def __init__(
        self,
        client: RawHttpResourceClient,
        *,
        warn: Callable[[str], None],
        secret_policy: SecretPreservationPolicy,
        field_diff: FieldDiff | None = None,
        verifier: ApplyVerifier | None = None,
        allow_unverified: bool = False,
    ) -> None:
        self._client = client
        self._warn = warn
        self._secret_policy = secret_policy
        self._field_diff = field_diff or FieldDiff()
        self._verifier = verifier or ApplyVerifier(secret_policy=secret_policy)
        self._allow_unverified = allow_unverified

    def prepare(
        self,
        spec: ResourceSpec,
        resource: Resource,
        payload: dict[str, Any],
        existing: dict[str, Any] | None,
    ) -> PreparedBody:
        write_payload = copy.deepcopy(payload)
        preserved, dropped = strip_encrypted_in_place(write_payload, spec)
        for path in dropped:
            self._warn(
                f"undeclared $encrypted$ at {spec.kind}.{path} dropped — "
                "declare in spec.secret_paths to silence"
            )
        if existing is None and preserved:
            raise BadRequest(
                f"{spec.kind} {resource.metadata.name!r} has placeholder secret(s) "
                f"at {', '.join(preserved)} — provide real values or pre-create "
                "the resource in AWX first"
            )
        preserved_fields, conflicts = self._secret_policy.partition(
            write_payload=write_payload, existing=existing, preserved=preserved
        )
        if conflicts:
            raise BadRequest(
                f"Cannot apply {spec.kind} {resource.metadata.name!r}: "
                f"{', '.join(sorted(conflicts))} contain a $encrypted$ placeholder "
                "alongside a sibling change. PATCH would overwrite the existing secret. "
                "Provide the actual secret value(s) or revert the sibling change(s)."
            )
        changes = self._field_diff.compute(
            existing=existing,
            desired=write_payload,
            preserved_fields=preserved_fields,
            server_enriched_fields=spec.server_enriched_fields,
        )
        if existing is not None:
            changed = {change.field for change in changes if change.note != PRESERVED_SECRET_NOTE}
            write_payload = {key: value for key, value in write_payload.items() if key in changed}
        return PreparedBody(write_payload, tuple(changes), tuple(preserved), tuple(dropped))

    def verify(
        self,
        *,
        spec: ResourceSpec,
        resource: Resource,
        payload: dict[str, Any],
        response: dict[str, Any],
        record_id: int | None,
    ) -> str | None:
        return self._verify_payload(
            spec=spec,
            resource=resource,
            payload=payload,
            sent_fields=tuple(payload),
            write_response=response,
            record_id=record_id,
        )

    def _verify_payload(
        self,
        *,
        spec: ResourceSpec,
        resource: Resource,
        payload: dict[str, Any],
        sent_fields: tuple[str, ...],
        write_response: dict[str, Any],
        record_id: int | None,
    ) -> str | None:
        """Verify that AWX reflected the fields sent in the body."""
        unreflected = self._verifier.unreflected_fields(
            spec,
            payload,
            write_response,
            fields=sent_fields,
        )
        fallback_error: AwxApiError | None = None
        if unreflected and record_id is not None:
            try:
                after = self._client.get(spec, record_id).model_dump()
            except AwxApiError as exc:
                fallback_error = exc
            else:
                unreflected = self._verifier.unreflected_fields(
                    spec,
                    payload,
                    after,
                    fields=sent_fields,
                )
        if not unreflected:
            return None
        detail = (
            "unverified field(s): "
            f"{', '.join(sorted(unreflected))}; requested state was not reflected by AWX"
        )
        if fallback_error is not None:
            detail = f"{detail}; fallback GET failed"
        if self._allow_unverified:
            self._warn(
                f"{spec.kind} {resource.metadata.name!r}: {detail}; "
                "continuing because --allow-unverified was set"
            )
            return detail
        message = (
            f"{spec.kind} {resource.metadata.name!r}: {detail}. "
            "Use --allow-unverified with --yes to keep an accepted-but-unproven write."
        )
        if fallback_error is not None:
            raise BadRequest(message) from fallback_error
        raise BadRequest(message)
