"""Tests for the root ``untaped capabilities`` command (Wave 1.4, spec §7.3).

Reports one record per candidate provider — ``name/status/distribution/
version`` — from the composition outcome, in name order. The listing never
touches settings, so invalid capability values cannot block it (spec §4
failure isolation).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_management.support import (
    ExtProfile,
    GithubProfile,
    JiraProfile,
    compose,
    make_spec,
    write_config,
)
from untaped import bootstrap
from untaped.capabilities.registry import (
    CompositionResult,
    ProviderCandidate,
    QuarantineRecord,
)
from untaped.management.capabilities import build_root_capabilities_app
from untaped.settings import get_settings
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


class _Provider:
    """Nullary provider double; raises ``error`` instead when given one."""

    def __init__(self, spec: object, error: Exception | None = None) -> None:
        self._spec = spec
        self._error = error

    def __call__(self) -> object:
        if self._error is not None:
            raise self._error
        return self._spec


def _candidate(
    name: str,
    *,
    distribution: str = "example-dist",
    version: str = "1.2.3",
    error: Exception | None = None,
) -> ProviderCandidate:
    return ProviderCandidate(
        distribution=distribution,
        name=name,
        target=_Provider(make_spec(name, profile_model=ExtProfile), error),
        distribution_version=version,
    )


def _rows(stdout: str) -> list[dict[str, object]]:
    return [dict(item) for item in json.loads(stdout)]


def _listing(candidates: list[ProviderCandidate]) -> list[dict[str, object]]:
    """The JSON rows ``untaped capabilities`` lists for ``candidates``."""
    root = bootstrap.build_root_app(candidates=candidates)
    invoked = CliInvoker().invoke(root.meta, ["capabilities", "--format", "json"])
    assert invoked.exit_code == 0, invoked.output
    return _rows(invoked.stdout)


def test_lists_a_ready_provider_with_its_distribution_version() -> None:
    rows = _listing([_candidate("acme", distribution="acme-dist")])
    assert rows == [
        {"name": "acme", "status": "ready", "distribution": "acme-dist", "version": "1.2.3"}
    ]


def test_the_listing_is_in_name_order_across_statuses() -> None:
    broken = _candidate("beta", distribution="aaa-dist", error=RuntimeError("x"))
    alpha = _candidate("alpha", distribution="zzz-dist")
    gamma = _candidate("gamma", distribution="mmm-dist")
    rows = _listing([gamma, broken, alpha])
    assert [(row["name"], row["status"]) for row in rows] == [
        ("alpha", "ready"),
        ("beta", "quarantined"),
        ("gamma", "ready"),
    ]


def test_quarantined_provider_lists_with_entry_point_name() -> None:
    candidate = ProviderCandidate(
        distribution="example-dist",
        name="ghost",
        target="example_mod:provider",
        distribution_version="0.1.0",
    )
    result = CompositionResult(
        capabilities=(),
        quarantine=(
            QuarantineRecord(
                distribution="example-dist",
                entry_point="example_mod:provider",
                reason="malformed-entry-point",
                detail="could not resolve entry point 'example_mod:provider'",
            ),
        ),
    )
    app = build_root_capabilities_app(result=result, candidates=(candidate,))
    invoked = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert invoked.exit_code == 0, invoked.output
    assert _rows(invoked.stdout) == [
        {
            "name": "ghost",
            "status": "quarantined",
            "distribution": "example-dist",
            "version": "0.1.0",
        }
    ]


def test_unresolvable_provider_uses_unknown_sentinels() -> None:
    candidate = ProviderCandidate(distribution="unknown", name="mystery", target="nope:missing")
    result = CompositionResult(
        capabilities=(),
        quarantine=(
            QuarantineRecord(
                distribution="unknown",
                entry_point="",
                reason="malformed-entry-point",
                detail="could not resolve entry point 'nope:missing'",
            ),
        ),
    )
    app = build_root_capabilities_app(result=result, candidates=(candidate,))
    invoked = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert invoked.exit_code == 0, invoked.output
    (row,) = _rows(invoked.stdout)
    assert row["name"] == "mystery"
    assert row["version"] == "unknown"


def test_table_headers_name_the_listing_contract() -> None:
    result = compose(make_spec("github", profile_model=GithubProfile))
    app = build_root_capabilities_app(result=result, candidates=())
    invoked = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert invoked.exit_code == 0, invoked.output
    header = [cell.strip() for cell in invoked.stdout.splitlines()[1].strip("│").split("│")]
    assert header == ["name", "status", "distribution", "version"]
    assert "github" in invoked.stdout
    listed = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert {"distribution", "version"} <= set(_rows(listed.stdout)[0])


def test_listing_is_not_blocked_by_invalid_settings(_isolated_config: Path) -> None:
    """The §4 isolation case: broken Jira values still list every capability."""
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    jira:\n      timeout: not-a-number\n",
    )
    get_settings.cache_clear()
    result = compose(
        make_spec("github", profile_model=GithubProfile),
        make_spec("jira", profile_model=JiraProfile),
    )
    app = build_root_capabilities_app(result=result, candidates=())
    invoked = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert invoked.exit_code == 0, invoked.output
    assert {row["name"] for row in _rows(invoked.stdout)} == {"github", "jira"}
