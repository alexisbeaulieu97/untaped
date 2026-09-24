"""Sweep query semantics: predicate labels, ref matching, validation, profile lattice."""

from __future__ import annotations

import pytest

from untaped.capabilities.github.domain.sweep import (
    RefEvaluation,
    SweepQuery,
    profile_join,
    ref_matches,
)


def test_labels_are_flag_value_pairs_in_stable_order_regardless_of_modifiers() -> None:
    query = SweepQuery(
        greps=("old_api", "requests"),
        not_greps=("new_api",),
        paths=("src/**",),
        has_files=("pyproject.toml",),
        lacks_files=("setup.py",),
        ignore_case=True,
        fixed_strings=True,
        word_regexp=True,
    )

    assert query.labels() == (
        "grep:old_api",
        "grep:requests",
        "not-grep:new_api",
        "has-file:pyproject.toml",
        "lacks-file:setup.py",
    )


_ALL = SweepQuery(greps=("old_api",), has_files=("pyproject.toml",))
_ANY = SweepQuery(
    greps=("log4j",),
    has_files=("pom.xml",),
    not_greps=("safe_version",),
    lacks_files=("blocked.txt",),
    any_mode=True,
)
_NEGATIONS = SweepQuery(not_greps=("old_api",), lacks_files=("setup.py",), any_mode=True)


@pytest.mark.parametrize(
    ("query", "hits", "matches"),
    [
        (_ALL, {"grep:old_api": 2, "has-file:pyproject.toml": 1}, True),
        (_ALL, {"grep:old_api": 2, "has-file:pyproject.toml": 0}, False),
        (_ANY, {"grep:log4j": 0, "has-file:pom.xml": 1}, True),
        (_ANY, {"grep:log4j": 2, "not-grep:safe_version": 1}, False),
        (_ANY, {"grep:log4j": 2, "lacks-file:blocked.txt": 1}, False),
        (_NEGATIONS, {"not-grep:old_api": 0, "lacks-file:setup.py": 0}, True),
        (_NEGATIONS, {"not-grep:old_api": 0, "lacks-file:setup.py": 1}, False),
    ],
    ids=[
        "all-hit",
        "all-one-miss",
        "any-one-positive",
        "any-not-grep-vetoes",
        "any-lacks-file-vetoes",
        "negations-clean",
        "negations-dirty",
    ],
)
def test_ref_matches_ands_or_ors_positives_and_negations_always_veto(
    query: SweepQuery, hits: dict[str, int], matches: bool
) -> None:
    assert ref_matches(query, RefEvaluation(ref="main", hits=hits)) is matches


@pytest.mark.parametrize(
    ("query", "message"),
    [
        (SweepQuery(), "requires at least one predicate"),
        (SweepQuery(paths=("src/**",), has_files=("pyproject.toml",)), "--has-file"),
    ],
)
def test_invalid_queries_are_rejected(query: SweepQuery, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        query.validate()


def test_profile_join_lattice() -> None:
    assert profile_join("default", "default") == "default"
    assert profile_join("default", "branches") == "branches"
    assert profile_join("default", "tags") == "tags"
    assert profile_join("branches", "default") == "branches"
    assert profile_join("tags", "default") == "tags"
    assert profile_join("branches", "tags") == "all"
    assert profile_join("tags", "branches") == "all"
    assert profile_join("all", "default") == "all"
    assert profile_join("all", "branches") == "all"
    assert profile_join("tags", "tags") == "tags"
