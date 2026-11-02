"""A command line the parser refuses is said in the house style, and exits 2."""

from __future__ import annotations

import pytest

from untaped.sdk.testing import invoke_root


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (
            ["config", "set", "demo.key", "--", "-q", "--dry-run"],
            "error: unexpected argument after --: '--dry-run'",
        ),
        (["config", "nosuch"], "error: command not found: 'nosuch'; known: list, get, set"),
        (["conifg"], "error: command not found: 'conifg'; did you mean 'config'?"),
        (["config", "get", "a", "b"], "error: unexpected argument: 'b'"),
        (["config", "get"], "error: config get: KEY requires an argument"),
        (["config", "list", "--columsn", "x"], "error: unknown option: --columsn; did you mean"),
        (["config", "list", "--format", "jsn"], 'error: invalid value "jsn" for --format'),
    ],
)
def test_a_refused_line_reads_as_untaped_errors_do(argv: list[str], said: str) -> None:
    result = invoke_root(argv)

    assert result.exit_code == 2
    assert said in result.stderr
    assert not result.stderr.rstrip().endswith(".")
