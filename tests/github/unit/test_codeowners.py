"""CODEOWNERS parsing follows GitHub's gitignore-style matching; the last matching rule wins."""

from __future__ import annotations

import pytest

from untaped.capabilities.github.domain.codeowners import parse_codeowners


@pytest.mark.parametrize(
    ("codeowners", "path", "owners"),
    [
        ("* @all\nsrc/** @platform\nsrc/api.py @api\n", "src/api.py", ("@api",)),
        ("# team\n\n* @all\ndocs/**\n", "docs/readme.md", ()),  # owner-less rule unsets
        ("[] @broken\n* @all\n", "README.md", ("@all",)),  # malformed lines ignored
        ("*.py @python\n", "api.py", ("@python",)),
        ("*.py @python\n", "src/api.py", ("@python",)),
        ("/build.yml @root\n", "build.yml", ("@root",)),
        ("/build.yml @root\n", "nested/build.yml", ()),
        ("docs/ @docs\n", "docs/readme.md", ("@docs",)),
        ("docs/ @docs\n", "src/docs/readme.md", ("@docs",)),
        ("apps/web @web\n", "apps/web/index.js", ("@web",)),
        ("apps/web @web\n", "apps/web", ("@web",)),
        ("apps/web @web\n", "x/apps/web/index.js", ()),
        ("docs @d\n", "src/docs/deep/readme.md", ("@d",)),
        ("docs @d\n", "documents/readme.md", ()),
        ("/src/* @s\n", "src/a.py", ("@s",)),
        ("/src/* @s\n", "src/a/b.py", ()),
        ("src/*.py @py\n", "src/nested/a.py", ()),
        ("**/logs @logs\n", "logs", ("@logs",)),
        ("**/logs @logs\n", "build/logs/app.log", ("@logs",)),
        ("**/logs @logs\n", "build/mylogs/app.log", ()),
        ("a/**/b @ab\n", "a/b", ("@ab",)),
        ("a/**/b @ab\n", "a/x/y/b/file.txt", ("@ab",)),
        ("a/**/b @ab\n", "z/a/b", ()),
    ],
)
def test_owners_for_path(codeowners: str, path: str, owners: tuple[str, ...]) -> None:
    assert parse_codeowners(codeowners).owners_for(path) == owners


def test_default_owners_come_from_star_rule() -> None:
    rules = parse_codeowners("*.py @python\n* @all @backup\nREADME.md @docs\n")

    assert rules.default_owners() == ("@all", "@backup")
