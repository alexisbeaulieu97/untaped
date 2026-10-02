"""Post-write convergence checks for apply body payloads."""

from __future__ import annotations

from typing import Any

from untaped.capabilities.awx.application.apply_secret_policy import SecretPreservationPolicy
from untaped.capabilities.awx.application.mutation_values import semantic_equal
from untaped.capabilities.awx.domain import ResourceSpec


class ApplyVerifier:
    """Check whether requested body fields are reflected by an AWX record."""

    def __init__(self, *, secret_policy: SecretPreservationPolicy | None = None) -> None:
        self._secret_policy = secret_policy or SecretPreservationPolicy()

    def unreflected_fields(
        self,
        spec: ResourceSpec,
        desired: dict[str, Any],
        observed: dict[str, Any],
        *,
        fields: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Return body-field names whose requested value is not reflected.

        User-owned maps and lists must match exactly. Additional server keys
        are accepted only for the specification's explicit enrichment fields.
        """
        desired_clean = self._strip_secret_paths(spec, desired)
        observed_clean = self._strip_secret_paths(spec, observed)
        missing: list[str] = []
        for field in fields:
            if field not in desired_clean:
                continue
            if field not in observed_clean:
                missing.append(field)
                continue
            if not semantic_equal(
                desired_clean[field],
                observed_clean[field],
                allow_server_enrichment=field in spec.server_enriched_fields,
                structured_text=field in spec.structured_text_fields,
            ):
                missing.append(field)
        return tuple(missing)

    def _strip_secret_paths(self, spec: ResourceSpec, value: dict[str, Any]) -> dict[str, Any]:
        stripped = self._secret_policy.strip_paths(value, list(spec.secret_paths))
        if not isinstance(stripped, dict):
            return {}
        return stripped


__all__ = ["ApplyVerifier"]
