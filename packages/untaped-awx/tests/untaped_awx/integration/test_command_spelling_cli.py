"""AWX help lists the commands under their current spellings."""

from __future__ import annotations

from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.testing import invoke_cli


def test_help_lists_the_current_spellings(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)

    awx_help = invoke_cli(root, ["awx", "--help"]).stdout
    inventories_help = invoke_cli(root, ["awx", "inventories", "--help"]).stdout
    templates_help = invoke_cli(root, ["awx", "job-templates", "--help"]).stdout

    assert " export " in awx_help
    assert " apply " in awx_help
    assert "input-inventories" in inventories_help
    assert " export " in templates_help


def test_ping_options_are_keyword_only(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    result = invoke_cli(build_root_app(candidates=first_party_candidates), ["awx", "ping", "json"])

    assert result.exit_code == 2, result.output
