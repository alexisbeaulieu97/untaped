"""Unit tests for shared CLI scope parsing helpers."""

from __future__ import annotations

import pytest

from untaped.api import ConfigError
from untaped.capabilities.github.application import TeamScope
from untaped.capabilities.github.cli._scopes import parse_team_scopes


def test_parse_team_scopes_accepts_repeated_org_slug_values() -> None:
    scopes = parse_team_scopes(["acme/backend", "platform/ops"])

    assert scopes == (TeamScope(org="acme", slug="backend"), TeamScope(org="platform", slug="ops"))


def test_parse_team_scopes_expands_bare_slug_with_one_org() -> None:
    scopes = parse_team_scopes(["backend"], orgs=("acme",))

    assert scopes == (TeamScope(org="acme", slug="backend"),)


@pytest.mark.parametrize("value", ["backend", "acme/backend/extra", "/backend", "acme/"])
def test_parse_team_scopes_rejects_malformed_values(value: str) -> None:
    with pytest.raises(ConfigError, match="ORG/SLUG"):
        parse_team_scopes([value])


def test_parse_team_scopes_rejects_bare_slug_with_multiple_orgs() -> None:
    with pytest.raises(ConfigError, match="exactly one --org"):
        parse_team_scopes(["backend"], orgs=("acme", "platform"))
