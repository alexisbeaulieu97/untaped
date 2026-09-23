from __future__ import annotations

from untaped.capabilities.github.domain.codeowners import parse_codeowners


def test_last_matching_rule_wins() -> None:
    rules = parse_codeowners(
        """
* @all
src/** @platform
src/api.py @api
"""
    )

    assert rules.owners_for("src/api.py") == ("@api",)


def test_unrooted_pattern_matches_any_depth() -> None:
    rules = parse_codeowners("*.py @python\n")

    assert rules.owners_for("api.py") == ("@python",)
    assert rules.owners_for("src/api.py") == ("@python",)


def test_rooted_pattern_matches_from_root_only() -> None:
    rules = parse_codeowners("/build.yml @root\n")

    assert rules.owners_for("build.yml") == ("@root",)
    assert rules.owners_for("nested/build.yml") == ()


def test_directory_rule_owns_contained_files() -> None:
    rules = parse_codeowners("docs/ @docs\n")

    assert rules.owners_for("docs/readme.md") == ("@docs",)
    assert rules.owners_for("src/docs/readme.md") == ("@docs",)


def test_owner_less_rule_unsets_owners() -> None:
    rules = parse_codeowners(
        """
* @all
docs/**
"""
    )

    assert rules.owners_for("docs/readme.md") == ()


def test_malformed_lines_are_ignored() -> None:
    rules = parse_codeowners(
        """
[] @broken
* @all
"""
    )

    assert rules.owners_for("README.md") == ("@all",)


def test_default_owners_come_from_star_rule() -> None:
    rules = parse_codeowners(
        """
*.py @python
* @all @backup
README.md @docs
"""
    )

    assert rules.default_owners() == ("@all", "@backup")


def test_anchored_path_pattern_owns_directory_contents() -> None:
    rules = parse_codeowners("apps/web @web\n")

    assert rules.owners_for("apps/web/index.js") == ("@web",)
    assert rules.owners_for("apps/web") == ("@web",)
    assert rules.owners_for("x/apps/web/index.js") == ()


def test_unanchored_name_owns_matching_directory_at_any_depth() -> None:
    rules = parse_codeowners("docs @d\n")

    assert rules.owners_for("docs/readme.md") == ("@d",)
    assert rules.owners_for("src/docs/deep/readme.md") == ("@d",)
    assert rules.owners_for("documents/readme.md") == ()


def test_trailing_star_owns_direct_children_only() -> None:
    rules = parse_codeowners("/src/* @s\n")

    assert rules.owners_for("src/a.py") == ("@s",)
    assert rules.owners_for("src/a/b.py") == ()


def test_single_star_does_not_cross_slashes() -> None:
    rules = parse_codeowners("src/*.py @py\n")

    assert rules.owners_for("src/a.py") == ("@py",)
    assert rules.owners_for("src/nested/a.py") == ()


def test_leading_double_star_matches_root_and_nested() -> None:
    rules = parse_codeowners("**/logs @logs\n")

    assert rules.owners_for("logs") == ("@logs",)
    assert rules.owners_for("logs/app.log") == ("@logs",)
    assert rules.owners_for("build/logs/app.log") == ("@logs",)
    assert rules.owners_for("build/mylogs/app.log") == ()


def test_middle_double_star_matches_zero_or_more_directories() -> None:
    rules = parse_codeowners("a/**/b @ab\n")

    assert rules.owners_for("a/b") == ("@ab",)
    assert rules.owners_for("a/x/y/b/file.txt") == ("@ab",)
    assert rules.owners_for("z/a/b") == ()
