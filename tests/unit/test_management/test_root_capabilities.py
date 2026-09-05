"""Tests for the root ``untaped capabilities`` command (Wave 1.4, spec §7.3).

Reports one record per candidate provider — ``name/origin/status/
distribution/version/api`` — from the composition outcome. The listing never
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
    ExternalProvider,
    QuarantineRecord,
)
from untaped.management.capabilities import build_root_capabilities_app
from untaped.settings import get_settings
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


class _Provider:
    """Nullary external provider double."""

    def __init__(self, spec: object) -> None:
        self.api_requires = (1.0, 2.0)
        self._spec = spec

    def __call__(self) -> object:
        return self._spec


def _external(
    name: str, *, distribution: str = "example-dist", version: str = "1.2.3"
) -> ExternalProvider:
    return ExternalProvider(
        distribution=distribution,
        name=name,
        target=_Provider(make_spec(name, profile_model=ExtProfile)),
        distribution_version=version,
    )


def _rows(stdout: str) -> list[dict[str, object]]:
    return [dict(item) for item in json.loads(stdout)]


def test_lists_ready_builtin_and_external() -> None:
    github = make_spec("github", profile_model=GithubProfile)
    result = compose(github)
    external = _external("ext")
    composed = bootstrap.compose_root(builtins=(), externals=(external,))
    merged = CompositionResult(
        capabilities=result.capabilities + composed.capabilities,
        quarantine=(),
    )
    app = build_root_capabilities_app(
        result=merged,
        candidates=(external,),
        shell_distribution="untaped",
    )
    invoked = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert invoked.exit_code == 0, invoked.output
    rows = {row["name"]: row for row in _rows(invoked.stdout)}
    assert rows["github"]["status"] == "ready"
    assert rows["github"]["origin"] == "built-in"
    assert rows["github"]["distribution"] == "untaped"
    assert rows["ext"]["status"] == "ready"
    assert rows["ext"]["origin"] == "external"
    assert rows["ext"]["distribution"] == "example-dist"
    assert rows["ext"]["version"] == "1.2.3"
    assert rows["ext"]["api"] == ">=1.0,<2.0"


def test_quarantined_provider_lists_with_entry_point_name() -> None:
    candidate = ExternalProvider(
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
    app = build_root_capabilities_app(
        result=result, candidates=(candidate,), shell_distribution="untaped"
    )
    invoked = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert invoked.exit_code == 0, invoked.output
    (row,) = _rows(invoked.stdout)
    assert row["name"] == "ghost"
    assert row["status"] == "quarantined"
    assert row["origin"] == "external"
    assert row["distribution"] == "example-dist"
    assert row["version"] == "0.1.0"
    assert row["api"] == "unknown"


def test_unresolvable_provider_uses_unknown_sentinels() -> None:
    candidate = ExternalProvider(distribution="unknown", name="mystery", target="nope:missing")
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
    app = build_root_capabilities_app(
        result=result, candidates=(candidate,), shell_distribution="untaped"
    )
    invoked = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert invoked.exit_code == 0, invoked.output
    (row,) = _rows(invoked.stdout)
    assert row["name"] == "mystery"
    assert row["version"] == "unknown"
    assert row["api"] == "unknown"


def test_table_headers_name_the_listing_contract() -> None:
    result = compose(make_spec("github", profile_model=GithubProfile))
    app = build_root_capabilities_app(result=result, candidates=(), shell_distribution="untaped")
    invoked = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert invoked.exit_code == 0, invoked.output
    for column in ("name", "origin", "status", "distribution", "version", "api"):
        assert column in invoked.stdout
    assert "github" in invoked.stdout


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
    app = build_root_capabilities_app(result=result, candidates=(), shell_distribution="untaped")
    invoked = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert invoked.exit_code == 0, invoked.output
    assert {row["name"] for row in _rows(invoked.stdout)} == {"github", "jira"}
