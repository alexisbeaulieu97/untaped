"""Row-13 distribution-metadata checks: group and Requires-Dist (spec §§5, 7.2)."""

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
    Provider,
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
    )
    assert candidate.requires_dist == ("untaped>=0",)


def test_row13_discover_without_distributions() -> None:
    assert discover_external_providers(group="untaped.capabilities.no-such-group") == ()


def test_row13_requires_dist_checked_before_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admitting Requires-Dist must quarantine without importing (spec §5)."""
    import untaped.capabilities.registry as registry

    calls: list[str] = []
    real_import = registry.import_module

    def tracking_import(name: str, *args: object, **kwargs: object) -> object:
        calls.append(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(registry, "import_module", tracking_import)
    candidate = ExternalProvider(
        distribution="example-dist",
        name="pinned",
        target="definitely.missing.row13_module:provider",
        requires_dist=("untaped>=99",),
    )
    result = compose(make_shell(), [], [candidate])
    assert calls == []
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert "untaped>=99" in record.detail


def test_row13_group_checked_before_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong entry-point group must quarantine without importing (spec §5)."""
    import untaped.capabilities.registry as registry

    calls: list[str] = []
    real_import = registry.import_module

    def tracking_import(name: str, *args: object, **kwargs: object) -> object:
        calls.append(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(registry, "import_module", tracking_import)
    candidate = ExternalProvider(
        distribution="example-dist",
        name="grouped",
        target="definitely.missing.row13_module:provider",
        entry_point_group="other.group",
    )
    result = compose(make_shell(), [], [candidate])
    assert calls == []
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert "other.group" in record.detail


def test_row13_live_discovery_composes_without_loader_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live discovery path carries no loader gate and composes end to end."""
    import sys
    import types
    from types import SimpleNamespace

    import untaped.capabilities.registry as registry

    assert not hasattr(registry, "_check_loader_fields")
    spec = make_spec(name="live-cap")
    module = types.ModuleType("test_live_row13_provider_mod")
    module.provider = Provider(spec)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "test_live_row13_provider_mod", module)

    fake_dist = SimpleNamespace(
        version="1.0.0",
        requires=["httpx>=1"],
        metadata={"Name": "live-dist"},
    )

    fake_entry_point = SimpleNamespace(
        name="live-cap",
        value="test_live_row13_provider_mod:provider",
        group="untaped.capabilities",
        dist=fake_dist,
    )
    monkeypatch.setattr(
        registry.importlib_metadata,
        "entry_points",
        lambda group=None: [fake_entry_point]
        if group == "untaped.capabilities"
        else [],
    )
    providers = registry.discover_external_providers()
    assert len(providers) == 1
    (candidate,) = providers
    assert candidate.distribution == "live-dist"
    assert candidate.name == "live-cap"
    assert not hasattr(candidate, "loader_fields")
    result = compose(make_shell(), [], list(providers))
    assert [cap.spec.name for cap in result.capabilities] == ["live-cap"]
    assert result.quarantine == ()
