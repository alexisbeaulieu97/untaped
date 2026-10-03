"""AWX help lists only the current command spellings; removed ones are in tests/repo."""

from __future__ import annotations

from untaped.bootstrap import build_root_app
from untaped.capabilities.registry import ProviderCandidate
from untaped.testing import invoke_cli


def test_help_lists_only_the_current_spellings(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)

    awx_help = invoke_cli(root, ["awx", "--help"]).stdout
    inventories_help = invoke_cli(root, ["awx", "inventories", "--help"]).stdout
    templates_help = invoke_cli(root, ["awx", "job-templates", "--help"]).stdout

    assert " save " not in awx_help
    assert " export " in awx_help
    assert " apply " in awx_help
    assert "input-inventories" in inventories_help
    assert " apply " not in templates_help
    assert " export " in templates_help


def test_ping_options_are_keyword_only(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    result = invoke_cli(build_root_app(candidates=first_party_candidates), ["awx", "ping", "json"])

    assert result.exit_code == 2, result.output
