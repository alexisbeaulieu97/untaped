"""Compute exact body-field diffs and annotate preserved secret fields.

Body maps and ordered lists use the shared semantic comparator. Enrichment is
accepted only for explicitly declared fields; sub-endpoint memberships own their
separate set/order semantics.
"""

from __future__ import annotations

from typing import Any

from untaped.capabilities.awx.application.mutation_values import semantic_equal
from untaped.capabilities.awx.domain import FieldChange

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
        server_enriched_fields: tuple[str, ...] = (),
        structured_text_fields: tuple[str, ...] = (),
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
            if not semantic_equal(
                after,
                before,
                allow_server_enrichment=field in server_enriched_fields,
                structured_text=field in structured_text_fields,
            ):
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
