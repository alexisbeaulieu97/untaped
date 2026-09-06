"""Post-write convergence checks for apply body payloads."""

from __future__ import annotations

import json
from typing import Any

import yaml

from untaped.capabilities.awx.application.apply_secret_policy import SecretPreservationPolicy
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

        This is deliberately asymmetric: AWX may enrich structured fields with
        defaults, so every desired value must be present in the observed value,
        but observed values may carry extra keys.
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
            if not _is_reflected(desired_clean[field], observed_clean[field]):
                missing.append(field)
        return tuple(missing)

    def _strip_secret_paths(self, spec: ResourceSpec, value: dict[str, Any]) -> dict[str, Any]:
        stripped = self._secret_policy.strip_paths(value, list(spec.secret_paths))
        if not isinstance(stripped, dict):
            return {}
        return stripped


def _is_reflected(desired: Any, observed: Any) -> bool:
    desired = _parse_structured_string(desired)
    observed = _parse_structured_string(observed)
    if isinstance(desired, dict):
        if not isinstance(observed, dict):
            return False
        return all(
            key in observed and _is_reflected(desired_value, observed[key])
            for key, desired_value in desired.items()
        )
    if isinstance(desired, list):
        if not isinstance(observed, list):
            return False
        return _list_reflected(desired, observed)
    return bool(desired == observed)


def _list_reflected(desired: list[Any], observed: list[Any]) -> bool:
    # Dicts are subset-checked because AWX may add defaults; lists are
    # replacement values, so an extra observed item means the request diverged.
    if len(desired) != len(observed):
        return False
    unused = list(range(len(observed)))
    for desired_item in desired:
        match_index: int | None = None
        for index in unused:
            if _is_reflected(desired_item, observed[index]):
                match_index = index
                break
        if match_index is None:
            return False
        unused.remove(match_index)
    return True


def _parse_structured_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    for parser in (json.loads, yaml.safe_load):
        try:
            parsed = parser(value)
        except ValueError:
            continue
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict | list):
            return parsed
    return value


__all__ = ["ApplyVerifier"]
