"""Row-13 distribution-metadata checks: group, Requires-Dist, loader (spec §§5, 7.2)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from untaped.capabilities.registry import (
    CAPABILITIES_ENTRY_POINT_GROUP,
    ExternalProvider,
    compose,
    discover_external_providers,
)

from test_capabilities.capharness import (
    make_external,
    make_shell,
    make_spec,
)


def test_row13_wrong_group_quarantines() -> None:
    spec = make_spec(name="grouped")
    candidate = make_external(spec, "example-dist", entry_point_group="other.group")
    result = compose(make_shell(), [], [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert "other.group" in record.detail


def test_row13_group_default_is_capabilities_group() -> None:
    spec = make_spec(name="grouped")
    candidate = make_external(spec, "example-dist")
    assert candidate.entry_point_group == "untaped.capabilities"
    assert candidate.entry_point_group == CAPABILITIES_ENTRY_POINT_GROUP
    result = compose(make_shell(), [], [candidate])
    assert [cap.spec.name for cap in result.capabilities] == ["grouped"]
    assert result.quarantine == ()


def test_row13_non_admitting_requires_dist_quarantines() -> None:
    spec = make_spec(name="pinned")
    candidate = make_external(spec, "example-dist", requires_dist=("untaped>=99",))
    result = compose(make_shell(), [], [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert "untaped>=99" in record.detail


def test_row13_admitting_requires_dist_passes() -> None:
    spec = make_spec(name="pinned")
    candidate = make_external(spec, "example-dist", requires_dist=("untaped>=0",))
    result = compose(make_shell(), [], [candidate])
    assert [cap.spec.name for cap in result.capabilities] == ["pinned"]
    assert result.quarantine == ()


def test_row13_ignores_non_untaped_requires_dist() -> None:
    spec = make_spec(name="other-req")
    candidate = make_external(spec, "example-dist", requires_dist=("httpx>=99",))
    result = compose(make_shell(), [], [candidate])
    assert [cap.spec.name for cap in result.capabilities] == ["other-req"]
    assert result.quarantine == ()


def test_row13_malformed_requires_dist_quarantines() -> None:
    spec = make_spec(name="bad-req")
    candidate = make_external(spec, "example-dist", requires_dist=("untaped=>",))
    result = compose(make_shell(), [], [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert "untaped=>" in record.detail


def test_row13_direct_reference_requires_dist_passes() -> None:
    spec = make_spec(name="direct-req")
    candidate = make_external(
        spec,
        "example-dist",
        requires_dist=("untaped@ git+https://example.invalid/untaped.git@main",),
    )
    result = compose(make_shell(), [], [candidate])
    assert [cap.spec.name for cap in result.capabilities] == ["direct-req"]
    assert result.quarantine == ()


def test_row13_unknown_loader_field_quarantines() -> None:
    spec = make_spec(name="loaded")
    candidate = make_external(spec, "example-dist", loader_fields=("bogus_field",))
    result = compose(make_shell(), [], [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert "bogus_field" in record.detail


def test_row13_known_loader_fields_pass() -> None:
    spec = make_spec(name="loaded")
    candidate = make_external(
        spec,
        "example-dist",
        loader_fields=("name", "app_factory", "config_section", "profile_model"),
    )
    result = compose(make_shell(), [], [candidate])
    assert [cap.spec.name for cap in result.capabilities] == ["loaded"]
    assert result.quarantine == ()


def test_row13_multi_entry_point_distribution_passes() -> None:
    first = make_external(
        make_spec(name="alpha"), "multi-dist", distribution_version="1.2.3"
    )
    second = make_external(
        make_spec(name="beta"), "multi-dist", distribution_version="1.2.3"
    )
    result = compose(make_shell(), [], [second, first])
    assert [cap.spec.name for cap in result.capabilities] == ["alpha", "beta"]
    assert result.quarantine == ()


def test_row13_external_provider_is_frozen() -> None:
    candidate = make_external(make_spec(name="frozen"))
    with pytest.raises(FrozenInstanceError):
        candidate.distribution_version = "9.9.9"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        candidate.entry_point_group = "other"  # type: ignore[misc]


def test_row13_sequences_normalize_to_tuples() -> None:
    candidate = ExternalProvider(
        distribution="d",
        name="n",
        target="m:a",
        requires_dist=["untaped>=0"],
        loader_fields=["name"],
    )
    assert candidate.requires_dist == ("untaped>=0",)
    assert candidate.loader_fields == ("name",)


def test_row13_discover_without_distributions() -> None:
    assert discover_external_providers(group="untaped.capabilities.no-such-group") == ()
