"""Unit tests for FieldDiff.

Pure value-shaped class: compute(existing, desired, *, preserved_fields)
returns the list of FieldChange rows the apply pipeline emits as the
preview diff. No I/O, no Protocols.
"""

from __future__ import annotations

from untaped_awx.application.apply_field_diff import FieldDiff
from untaped_awx.domain import FieldChange


def test_compute_emits_addition_rows_when_existing_is_none() -> None:
    """Create path: every desired field is a new addition with
    ``before=None``."""
    diff = FieldDiff()
    changes = diff.compute(
        existing=None,
        desired={"name": "deploy", "playbook": "deploy.yml"},
        preserved_fields=set(),
    )
    assert {c.field for c in changes} == {"name", "playbook"}
    for c in changes:
        assert c.before is None
        assert c.note is None


def test_compute_marks_preserved_secret_addition_on_create() -> None:
    """A field in ``preserved_fields`` on the create path is annotated
    with the preserved-secret note even though it's a new addition."""
    diff = FieldDiff()
    changes = diff.compute(
        existing=None,
        desired={"webhook_key": "secret-on-create"},
        preserved_fields={"webhook_key"},
    )
    assert len(changes) == 1
    assert changes[0].field == "webhook_key"
    assert changes[0].note == "preserved existing secret"


def test_compute_skips_unchanged_fields_on_update() -> None:
    """Fields whose existing value equals the desired value produce no
    change row (the diff stays quiet)."""
    diff = FieldDiff()
    changes = diff.compute(
        existing={"name": "deploy", "playbook": "deploy.yml"},
        desired={"name": "deploy", "playbook": "deploy.yml"},
        preserved_fields=set(),
    )
    assert changes == []


def test_compute_emits_change_rows_for_modified_fields() -> None:
    diff = FieldDiff()
    changes = diff.compute(
        existing={"name": "deploy", "playbook": "old.yml"},
        desired={"name": "deploy", "playbook": "new.yml"},
        preserved_fields=set(),
    )
    assert [(c.field, c.before, c.after) for c in changes] == [("playbook", "old.yml", "new.yml")]


def test_compute_treats_lists_as_order_insensitive() -> None:
    """FK lists like ``credentials`` are sets semantically; reordering
    them server-side must not produce a spurious diff row."""
    diff = FieldDiff()
    changes = diff.compute(
        existing={"credentials": [10, 11]},
        desired={"credentials": [11, 10]},
        preserved_fields=set(),
    )
    assert changes == []


def test_compute_falls_back_to_equality_for_unsortable_lists() -> None:
    """Lists of unsortable items (mixed types, dicts) compare with
    ``==`` directly — order matters in that case."""
    diff = FieldDiff()
    changes_equal = diff.compute(
        existing={"things": [{"a": 1}, {"b": 2}]},
        desired={"things": [{"a": 1}, {"b": 2}]},
        preserved_fields=set(),
    )
    assert changes_equal == []
    changes_diff = diff.compute(
        existing={"things": [{"a": 1}]},
        desired={"things": [{"a": 2}]},
        preserved_fields=set(),
    )
    assert len(changes_diff) == 1
    assert changes_diff[0].field == "things"


def test_compute_emits_preserved_secret_rows_for_present_fields() -> None:
    """Preserved-field present in ``desired`` (still a value, just
    marked preserved) emits a row showing the existing value retained."""
    diff = FieldDiff()
    changes = diff.compute(
        existing={"inputs": {"u": "u", "p": "secret"}},
        desired={"inputs": {"u": "u"}},  # secret stripped, sibling unchanged
        preserved_fields={"inputs"},
    )
    assert len(changes) == 1
    assert changes[0].field == "inputs"
    assert changes[0].note == "preserved existing secret"
    # before == after because we keep the existing value
    assert changes[0].before == {"u": "u", "p": "secret"}
    assert changes[0].after == {"u": "u", "p": "secret"}


def test_compute_emits_preserved_row_for_field_entirely_stripped() -> None:
    """Top-level secret field stripped from ``desired`` (e.g.
    ``webhook_key`` was a placeholder, fully removed) still gets a row
    so the user sees what's preserved."""
    diff = FieldDiff()
    changes = diff.compute(
        existing={"webhook_key": "secret-value"},
        desired={},  # webhook_key removed entirely
        preserved_fields={"webhook_key"},
    )
    assert len(changes) == 1
    assert changes[0].field == "webhook_key"
    assert changes[0].note == "preserved existing secret"
    assert changes[0].before == "secret-value"
    assert changes[0].after == "secret-value"


def test_compute_does_not_duplicate_preserved_row_when_field_present_in_desired() -> None:
    """A preserved field that's also in ``desired`` shouldn't produce
    two rows — one from the desired-field loop, one from the
    stripped-field fallback."""
    diff = FieldDiff()
    changes = diff.compute(
        existing={"inputs": {"u": "u"}},
        desired={"inputs": {"u": "u"}},
        preserved_fields={"inputs"},
    )
    matching = [c for c in changes if c.field == "inputs"]
    assert len(matching) == 1


def test_compute_returns_FieldChange_instances() -> None:
    diff = FieldDiff()
    changes = diff.compute(
        existing=None,
        desired={"name": "n"},
        preserved_fields=set(),
    )
    assert all(isinstance(c, FieldChange) for c in changes)
