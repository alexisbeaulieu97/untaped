"""The ``awx test init`` starter suite's comments name commands that parse against the real CLI."""

from __future__ import annotations

from untaped.bootstrap import build_root_app
from untaped.conventions.commands import comment_commands, parse_problem
from untaped.plugins.registry import PluginCandidate
from untaped_awx.domain.suites.suite_starter import starter_suite


def test_the_starter_suite_comments_name_real_commands(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    text = starter_suite("Deploy", organization="Default", launch={}, survey=[])
    commands = dict.fromkeys(comment_commands(text))

    assert commands
    assert [
        f"{command}: {problem}"
        for command, inline in commands
        if (problem := parse_problem(root, command, inline=inline)) is not None
    ] == []
