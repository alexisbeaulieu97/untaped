"""Capability distribution-metadata enforcement.

This module is the CI-mode metadata validator: it runs on every pull
request as part of the default ``pytest`` run. Compose mode stays lenient
for every provider (quarantine, never raise), first-party ones included.

Every provider (checked without importing provider code): capabilities
declared in the ``untaped.capabilities`` entry-point group, each
entry-point name equal to its capability ``name``, a non-empty
distribution name, and ``Requires-Dist`` on ``untaped`` admitting the
running SDK version.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import untaped.capabilities.registry as registry
from test_capabilities.capharness import Provider, make_candidate, make_shell, make_spec
from untaped.capabilities.registry import (
    ProviderCandidate,
    compose,
    discover_candidates,
)


@pytest.mark.parametrize(
    ("distribution", "name", "kwargs", "named"),
    [
        ("example-dist", "grouped", {"entry_point_group": "other.group"}, ["other.group"]),
        ("example-dist", "pinned", {"requires_dist": ("untaped>=99",)}, ["untaped>=99"]),
        ("example-dist", "bad-req", {"requires_dist": ("untaped=>",)}, ["untaped=>"]),
        ("example-dist", "alias", {}, ["'alias'", "'real'"]),
        ("   ", "real", {}, ["empty distribution name"]),
    ],
    ids=["wrong-group", "non-admitting", "malformed-requires", "name-mismatch", "no-distribution"],
)
def test_bad_candidate_metadata_quarantines(
    distribution: str, name: str, kwargs: dict[str, object], named: list[str]
) -> None:
    candidate = make_candidate(make_spec(name="real"), distribution, name=name, **kwargs)
    result = compose(make_shell(), [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert record.distribution == (distribution.strip() or "unknown")
    for text in named:
        assert text in record.detail


@pytest.mark.parametrize(
    "candidate_kwargs",
    [{"requires_dist": ("untaped>=99",)}, {"entry_point_group": "other.group"}],
    ids=["requires-dist", "group"],
)
def test_metadata_is_checked_before_import(
    monkeypatch: pytest.MonkeyPatch, candidate_kwargs: dict[str, object]
) -> None:
    """Bad metadata must quarantine without importing provider code."""
    calls: list[str] = []
    real_import = registry.import_module

    def tracking_import(name: str, *args: object, **kwargs: object) -> object:
        calls.append(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(registry, "import_module", tracking_import)
    candidate = ProviderCandidate(
        distribution="example-dist",
        name="pinned",
        target="definitely.missing.row13_module:provider",
        **candidate_kwargs,  # type: ignore[arg-type]
    )
    result = compose(make_shell(), [candidate])
    assert calls == []
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"


def _compose_with_sdk_version(
    monkeypatch: pytest.MonkeyPatch, sdk_version: str, requirement: str
) -> object:
    real_version = registry.importlib_metadata.version

    def fake_version(name: str) -> str:
        return sdk_version if name == "untaped" else real_version(name)

    monkeypatch.setattr(registry.importlib_metadata, "version", fake_version)
    candidate = make_candidate(
        make_spec(name="pep440"), "example-dist", requires_dist=(requirement,)
    )
    return compose(make_shell(), [candidate])


@pytest.mark.parametrize(
    ("sdk_version", "requirement", "admitted"),
    [
        ("6.1.0.dev3", "untaped>=6.1.0", False),
        ("6.1.0rc1", "untaped>=6.1.0", False),
        ("6.1.0.dev3", "untaped>=6.1.0.dev1", True),
        ("6.1.0", "untaped>=0", True),
        ("6.1.0", "untaped~=6.0", True),
        ("7.0.0", "untaped~=6.0", False),
        ("6.1.0.post1", "untaped==6.1.0", False),
        ("6.1.0", "untaped==6.1.*", True),
        ("6.1.0", "untaped[extra]>=6,<7", True),
        ("6.1.0", "untaped>=99; extra == 'dev'", True),
        ("6.1.0", "untaped>=99; python_version < '3'", True),
        ("6.1.0", "untaped>=99; python_version >= '3'", False),
        # Requirements on other distributions and direct references are ignored.
        ("6.1.0", "httpx>=99", True),
        ("6.1.0", "untaped@ git+https://example.invalid/untaped.git@main", True),
    ],
)
def test_requires_dist_follows_pep_440_and_508(
    monkeypatch: pytest.MonkeyPatch, sdk_version: str, requirement: str, admitted: bool
) -> None:
    result = _compose_with_sdk_version(monkeypatch, sdk_version, requirement)
    names = [cap.spec.name for cap in result.capabilities]  # type: ignore[attr-defined]
    assert names == (["pep440"] if admitted else [])


def test_malformed_marker_quarantines(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _compose_with_sdk_version(monkeypatch, "6.1.0", "untaped>=1; bogus ==")
    (record,) = result.quarantine  # type: ignore[attr-defined]
    assert record.reason == "bad-metadata"
    assert "malformed Requires-Dist" in record.detail


def test_multi_entry_point_distribution_passes() -> None:
    first = make_candidate(make_spec(name="alpha"), "multi-dist", distribution_version="1.2.3")
    second = make_candidate(make_spec(name="beta"), "multi-dist", distribution_version="1.2.3")
    result = compose(make_shell(), [second, first])
    assert [cap.spec.name for cap in result.capabilities] == ["alpha", "beta"]
    assert result.quarantine == ()


def test_discover_without_distributions() -> None:
    assert discover_candidates(group="untaped.capabilities.no-such-group") == ()


def test_live_discovery_composes_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
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
        lambda group=None: [fake_entry_point] if group == "untaped.capabilities" else [],
    )
    providers = registry.discover_candidates()
    (candidate,) = providers
    assert candidate.distribution == "live-dist"
    assert candidate.name == "live-cap"
    result = compose(make_shell(), list(providers))
    assert [cap.spec.name for cap in result.capabilities] == ["live-cap"]
    assert result.quarantine == ()


class _CountingDist:
    """Distribution double counting each metadata read."""

    def __init__(self, reads: list[str]) -> None:
        self._reads = reads

    @property
    def metadata(self) -> dict[str, str]:
        self._reads.append("metadata")
        return {"Name": "multi-dist"}

    @property
    def version(self) -> str:
        self._reads.append("version")
        return "2.0.0"

    @property
    def requires(self) -> list[str]:
        self._reads.append("requires")
        return ["untaped>=0"]


def test_discovery_reads_each_distribution_once(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []
    dist = _CountingDist(reads)
    entry_points = [
        SimpleNamespace(name=name, value=f"mod:{name}", group="untaped.capabilities", dist=dist)
        for name in ("alpha", "beta")
    ]
    monkeypatch.setattr(registry.importlib_metadata, "entry_points", lambda group: entry_points)
    candidates = registry.discover_candidates()
    assert [
        (c.name, c.distribution, c.distribution_version, c.requires_dist) for c in candidates
    ] == [
        ("alpha", "multi-dist", "2.0.0", ("untaped>=0",)),
        ("beta", "multi-dist", "2.0.0", ("untaped>=0",)),
    ]
    assert sorted(reads) == ["metadata", "requires", "version"]


def test_compose_resolves_the_running_version_once(monkeypatch: pytest.MonkeyPatch) -> None:
    looked_up: list[str] = []

    def fake_version(name: str) -> str:
        looked_up.append(name)
        return "6.1.0"

    monkeypatch.setattr(registry.importlib_metadata, "version", fake_version)
    candidates = [
        make_candidate(make_spec(name=name), "example-dist", requires_dist=("untaped>=6",))
        for name in ("alpha", "beta")
    ]
    result = compose(make_shell(), candidates)
    assert [cap.spec.name for cap in result.capabilities] == ["alpha", "beta"]
    assert looked_up == ["untaped"]
