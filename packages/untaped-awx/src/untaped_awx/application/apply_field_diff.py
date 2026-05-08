"""Compute the field-level diff that drives the apply preview.

Pure value-shaped class: takes the existing record + the (post-strip)
desired payload + the set of preserved-secret top-level fields, returns
``list[FieldChange]``. Order-insensitive equality is applied to FK
lists (``credentials``, etc.) so server-side reordering doesn't appear
as a spurious diff.

The diff is independent of the spec — it only reads the dicts. Tests
exercise it directly without a Catalog / FkResolver / Client.
"""

from __future__ import annotations

from typing import Any

from untaped_awx.domain import FieldChange


class FieldDiff:
    """Field-level diff for the apply pipeline preview."""

    def compute(
        self,
        *,
        existing: dict[str, Any] | None,
        desired: dict[str, Any],
        preserved_fields: set[str],
    ) -> list[FieldChange]:
        """Return field-level changes between existing and the (stripped) desired payload.

        ``desired`` is the post-strip payload (placeholders removed).
        Top-level fields in ``preserved_fields`` are emitted as
        ``preserved existing secret`` rows and are excluded from the
        PATCH so AWX retains the value (including any nested secrets).
        """
        out: list[FieldChange] = []
        if existing is None:
            for field, after in desired.items():
                note = "preserved existing secret" if field in preserved_fields else None
                out.append(FieldChange(field=field, before=None, after=after, note=note))
            return out
        for field, after in desired.items():
            before = existing.get(field)
            if field in preserved_fields:
                out.append(
                    FieldChange(
                        field=field,
                        before=before,
                        after=before,  # we keep the existing secret
                        note="preserved existing secret",
                    )
                )
                continue
            if not _equal(before, after):
                out.append(FieldChange(field=field, before=before, after=after))
        # Top-level secret fields entirely stripped from ``desired``
        # (e.g. ``webhook_key``) still need a row so the user sees them
        # in the preview.
        for field in preserved_fields:
            if field in desired:
                continue
            before = existing.get(field)
            out.append(
                FieldChange(
                    field=field,
                    before=before,
                    after=before,
                    note="preserved existing secret",
                )
            )
        return out


def _equal(a: Any, b: Any) -> bool:
    """Order-insensitive equality for FK lists (e.g., credentials)."""
    if isinstance(a, list) and isinstance(b, list):
        try:
            return bool(sorted(a, key=repr) == sorted(b, key=repr))
        except TypeError:
            return bool(a == b)
    return bool(a == b)
