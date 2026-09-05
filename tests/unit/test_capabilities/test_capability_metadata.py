"""Capability distribution-metadata enforcement (spec §7, Wave 1.6).

This module is the CI-mode metadata validator: it runs on every pull
request as part of the default ``pytest`` run. Compose mode stays lenient
for externals (quarantine, never raise); built-in violations are fatal
(an SDK bug).

Built-ins (§7.1): ``kind == "built-in"``, ``distribution == "untaped"``,
``entry_point == ""``, ``api_requires == (1.0, 2.0)``, and the reported
version is always the unified product version — never per-capability.

Externals (§7.2, checked without importing provider code): capabilities
declared in the ``untaped.capabilities`` entry-point group, each
entry-point name equal to its capability ``name``, a non-empty
distribution name, and ``Requires-Dist`` on ``untaped`` admitting the
running SDK version.
"""

from __future__ import annotations

import dataclasses
import tomllib
from importlib import metadata as importlib_metadata
from pathlib import Path

import pytest

from test_capabilities.capharness import make_external, make_shell, make_spec
from untaped.capabilities.registry import (
    VALID_REASONS,
    ProviderRef,
    QuarantineRecord,
    check_builtin_metadata,
    compose,
)
from untaped.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[3]

VALID_BUILTIN_REF = ProviderRef(
    kind="built-in",
    distribution="untaped",
    entry_point="",
    api_requires=(1.0, 2.0),
)


def test_builtin_ref_valid_passes() -> None:
    check_builtin_metadata(VALID_BUILTIN_REF)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "external"),
        ("distribution", "example-dist"),
        ("entry_point", "module:provider"),
        ("api_requires", (1.0, 1.0)),
        ("api_requires", (2.0, 3.0)),
    ],
)
def test_builtin_ref_violation_is_fatal(field: str, value: object) -> None:
    ref = dataclasses.replace(VALID_BUILTIN_REF, **{field: value})
    with pytest.raises(ConfigError, match="bad-metadata"):
        check_builtin_metadata(ref)


def test_builtin_commit_carries_builtin_ref() -> None:
    result = compose(make_shell(), [make_spec(name="builtin")], [])
    (registered,) = result.capabilities
    assert registered.provider_ref == VALID_BUILTIN_REF
    assert result.quarantine == ()


def test_builtin_version_is_the_product_version() -> None:
    try:
        installed = importlib_metadata.version("untaped")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("untaped distribution metadata is not installed")
    declared = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert installed == declared["project"]["version"]


def _quarantined(distribution: str, name: str, **kwargs: object) -> QuarantineRecord:
    candidate = make_external(make_spec(name=name), distribution, name=name, **kwargs)
    result = compose(make_shell(), [], [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert record.reason in VALID_REASONS
    return record


def test_external_wrong_group_quarantines_naming_group() -> None:
    record = _quarantined("example-dist", "grouped", entry_point_group="other.group")
    assert "other.group" in record.detail


def test_external_non_admitting_requires_dist_quarantines() -> None:
    record = _quarantined("example-dist", "pinned", requires_dist=("untaped>=99",))
    assert "untaped>=99" in record.detail


def test_external_malformed_requires_dist_quarantines() -> None:
    record = _quarantined("example-dist", "bad-req", requires_dist=("untaped=>",))
    assert "untaped=>" in record.detail


def test_external_name_mismatch_quarantines_naming_both() -> None:
    candidate = make_external(make_spec(name="real"), "example-dist", name="alias")
    result = compose(make_shell(), [], [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert "'alias'" in record.detail
    assert "'real'" in record.detail


def test_external_empty_distribution_quarantines_as_unknown() -> None:
    candidate = make_external(make_spec(name="nodist"), "   ", name="nodist")
    result = compose(make_shell(), [], [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert record.distribution == "unknown"
    assert "empty distribution name" in record.detail


def test_external_metadata_never_raises_in_compose_mode() -> None:
    candidates = [
        make_external(make_spec(name="grouped"), "example-dist", entry_point_group="other.group"),
        make_external(make_spec(name="pinned"), "example-dist", requires_dist=("untaped>=99",)),
        make_external(make_spec(name="real"), "example-dist", name="alias"),
    ]
    result = compose(make_shell(), [], candidates)
    assert result.capabilities == ()
    assert len(result.quarantine) == 3
    assert {record.reason for record in result.quarantine} == {"bad-metadata"}
