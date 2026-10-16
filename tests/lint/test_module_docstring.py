"""house.module-docstring: the bad example breaks the rule and the good one doesn't."""

from pathlib import Path

from untaped_lint.testing import repo_rule, run_rule, tree

RULE = repo_rule("module-docstring", root=Path(__file__).resolve().parents[2])

BAD = {"packages/untaped-x/src/untaped_x/a.py": "X = 1\n"}
GOOD = {
    "packages/untaped-x/src/untaped_x/a.py": '"""What a owns."""\n\nX = 1\n',
    "packages/untaped-x/src/untaped_x/__init__.py": (
        'from untaped_x.a import X\n\n__all__ = ["X"]\n'
    ),
    "packages/untaped-x/tests/test_a.py": "X = 1\n",
}


def test_module_docstring_flags_the_bad_example() -> None:
    found = run_rule(RULE, tree(BAD))
    assert [(each.rule, each.target) for each in found] == [
        ("module-docstring", "packages/untaped-x/src/untaped_x/a.py")
    ]


def test_module_docstring_passes_the_good_example() -> None:
    assert run_rule(RULE, tree(GOOD)) == []
