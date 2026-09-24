"""``--team`` parsing: ORG/SLUG, or a bare SLUG with exactly one ``--org``."""

from __future__ import annotations

import pytest

from untaped.capabilities.github.application import TeamScope
from untaped.capabilities.github.cli.scopes import parse_team_scopes
from untaped.capability_api import UsageError


@pytest.mark.parametrize(
    ("values", "orgs", "expected"),
    [
        (["acme/backend", "platform/ops"], (), (("acme", "backend"), ("platform", "ops"))),
        (["backend"], ("acme",), (("acme", "backend"),)),
    ],
)
def test_parse_team_scopes(
    values: list[str], orgs: tuple[str, ...], expected: tuple[tuple[str, str], ...]
) -> None:
    assert parse_team_scopes(values, orgs=orgs) == tuple(TeamScope(*pair) for pair in expected)


@pytest.mark.parametrize(
    ("value", "orgs"),
    [
        ("backend", ()),
        ("acme/backend/extra", ()),
        ("/backend", ()),
        ("acme/", ()),
        ("backend", ("acme", "platform")),
    ],
)
def test_parse_team_scopes_rejects_malformed_values(value: str, orgs: tuple[str, ...]) -> None:
    with pytest.raises(UsageError, match="ORG/SLUG unless exactly one --org"):
        parse_team_scopes([value], orgs=orgs)
