"""Unit tests for the FK id→name flattening helper used by ``--with-names``."""

from __future__ import annotations

from untaped_awx.cli._names import flatten_fks
from untaped_awx.domain import FkRef
from untaped_awx.infrastructure.spec import AwxResourceSpec


def _spec(*fk_refs: FkRef) -> AwxResourceSpec:
    return AwxResourceSpec(
        kind="Test",
        cli_name="tests",
        api_path="tests",
        identity_keys=("name",),
        canonical_fields=("name",),
        fk_refs=fk_refs,
        list_columns=("name",),
        commands=("list",),
    )


def test_scalar_fk_replaced_with_name_from_summary() -> None:
    spec = _spec(FkRef(field="project", kind="Project"))
    rows = [
        {
            "name": "deploy",
            "project": 10,
            "summary_fields": {"project": {"id": 10, "name": "playbooks"}},
        }
    ]
    out = flatten_fks(rows, spec)
    assert out == [
        {
            "name": "deploy",
            "project": "playbooks",
            "summary_fields": {"project": {"id": 10, "name": "playbooks"}},
        }
    ]


def test_scalar_fk_falls_back_to_id_when_summary_missing() -> None:
    spec = _spec(FkRef(field="project", kind="Project"))
    rows = [{"name": "deploy", "project": 10}]
    assert flatten_fks(rows, spec) == [{"name": "deploy", "project": 10}]


def test_scalar_fk_falls_back_to_id_when_summary_lacks_name_key() -> None:
    """If the summary entry is a dict without a ``name`` key, keep the id."""
    spec = _spec(FkRef(field="project", kind="Project"))
    rows = [
        {
            "name": "deploy",
            "project": 10,
            "summary_fields": {"project": {"id": 10}},
        }
    ]
    assert flatten_fks(rows, spec)[0]["project"] == 10


def test_multi_fk_replaced_with_list_of_names() -> None:
    spec = _spec(FkRef(field="credentials", kind="Credential", multi=True))
    rows = [
        {
            "name": "deploy",
            "credentials": [30, 31],
            "summary_fields": {
                "credentials": [
                    {"id": 30, "name": "ssh"},
                    {"id": 31, "name": "vault"},
                ]
            },
        }
    ]
    assert flatten_fks(rows, spec)[0]["credentials"] == ["ssh", "vault"]


def test_multi_fk_preserves_cardinality_when_summary_list_is_shorter() -> None:
    """Regression: a degraded server response with a shorter summary list
    must not silently drop trailing ids — fall back to the raw id at each
    index past the end of the summary."""
    spec = _spec(FkRef(field="credentials", kind="Credential", multi=True))
    rows = [
        {
            "name": "deploy",
            "credentials": [30, 31, 32],
            "summary_fields": {
                "credentials": [{"id": 30, "name": "ssh"}],
            },
        }
    ]
    assert flatten_fks(rows, spec)[0]["credentials"] == ["ssh", 31, 32]


def test_multi_fk_falls_back_to_id_for_summary_entry_without_name() -> None:
    spec = _spec(FkRef(field="credentials", kind="Credential", multi=True))
    rows = [
        {
            "name": "deploy",
            "credentials": [30, 31],
            "summary_fields": {
                "credentials": [
                    {"id": 30, "name": "ssh"},
                    {"id": 31},  # malformed — no name
                ]
            },
        }
    ]
    assert flatten_fks(rows, spec)[0]["credentials"] == ["ssh", 31]


def test_polymorphic_fk_is_skipped() -> None:
    """Schedule's polymorphic ``parent`` lives under a different wire key
    than the spec's logical name; ``flatten_fks`` defers to dotted columns."""
    spec = _spec(
        FkRef(field="parent", polymorphic=True, kind_in_value="kind"),
    )
    rows = [
        {
            "name": "nightly",
            "parent": 5,
            "summary_fields": {
                "unified_job_template": {"id": 5, "name": "deploy"},
            },
        }
    ]
    assert flatten_fks(rows, spec)[0]["parent"] == 5


def test_fk_with_none_value_is_left_alone() -> None:
    spec = _spec(FkRef(field="project", kind="Project"))
    rows = [{"name": "deploy", "project": None}]
    assert flatten_fks(rows, spec)[0]["project"] is None


def test_fk_field_missing_from_row_is_left_alone() -> None:
    spec = _spec(FkRef(field="project", kind="Project"))
    rows = [{"name": "deploy"}]
    assert flatten_fks(rows, spec) == [{"name": "deploy"}]


def test_returns_a_top_level_copy() -> None:
    spec = _spec(FkRef(field="project", kind="Project"))
    row = {
        "name": "deploy",
        "project": 10,
        "summary_fields": {"project": {"id": 10, "name": "playbooks"}},
    }
    out = flatten_fks([row], spec)
    assert out[0] is not row
    assert row["project"] == 10  # original untouched
