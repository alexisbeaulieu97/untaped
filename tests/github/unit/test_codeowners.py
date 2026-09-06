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
