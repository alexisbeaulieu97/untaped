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

PRESERVED_SECRET_NOTE = "preserved existing secret"
"""``FieldChange.note`` value emitted for top-level fields whose only
in-payload changes were secret-strip removals. Consumers (CLI render,
``ApplyResource._do_update``) read this exact string to filter out
preserved-secret rows from PATCH payloads and to pretty-print them in
the preview. Lifted to a constant so the producer (this module) and
the readers stay in sync without a copy-pasted literal."""


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
        :data:`PRESERVED_SECRET_NOTE` rows and are excluded from the
        PATCH so AWX retains the value (including any nested secrets).
        """
        out: list[FieldChange] = []
        if existing is None:
            for field, after in desired.items():
                note = PRESERVED_SECRET_NOTE if field in preserved_fields else None
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
                        note=PRESERVED_SECRET_NOTE,
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
                    note=PRESERVED_SECRET_NOTE,
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
