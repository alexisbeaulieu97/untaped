from __future__ import annotations

import pytest

from untaped.capabilities.github.domain.sweep import (
    RefEvaluation,
    RefSelector,
    RepoSweepOutcome,
    SweepQuery,
    profile_join,
    ref_matches,
)


def test_labels_are_flag_value_pairs_in_stable_order() -> None:
    query = SweepQuery(
        greps=("old_api", "requests"),
        not_greps=("new_api",),
        paths=("src/**",),
        has_files=("pyproject.toml",),
        lacks_files=("setup.py",),
        any_mode=False,
        refs=RefSelector(),
    )

    assert query.labels() == (
        "grep:old_api",
        "grep:requests",
        "not-grep:new_api",
        "has-file:pyproject.toml",
        "lacks-file:setup.py",
    )


def test_content_modifiers_default_false() -> None:
    query = SweepQuery(greps=("old_api",))

    assert query.ignore_case is False
    assert query.fixed_strings is False
    assert query.word_regexp is False


def test_content_modifiers_do_not_perturb_labels() -> None:
    query = SweepQuery(
        greps=("old_api",),
        not_greps=("new_api",),
        has_files=("pyproject.toml",),
        lacks_files=("setup.py",),
        ignore_case=True,
        fixed_strings=True,
        word_regexp=True,
    )

    assert query.labels() == (
        "grep:old_api",
        "not-grep:new_api",
        "has-file:pyproject.toml",
        "lacks-file:setup.py",
    )


def test_repo_sweep_outcome_carries_clone_url_for_pipe_record() -> None:
    outcome = RepoSweepOutcome(
        full_name="acme/api",
        clone_url="https://github.example.com/acme/api.git",
        matched=True,
        refs_matched=("main",),
        hits={"grep:old_api": 1},
        owners=("@team",),
        synced_at="2026-07-06T12:00:00+00:00",
    )

    assert outcome.clone_url == "https://github.example.com/acme/api.git"


def test_all_positive_predicates_must_hit() -> None:
    query = SweepQuery(
        greps=("old_api",),
        has_files=("pyproject.toml",),
        not_greps=(),
        paths=(),
        lacks_files=(),
        any_mode=False,
        refs=RefSelector(),
    )

    assert ref_matches(
        query,
        RefEvaluation(ref="main", hits={"grep:old_api": 2, "has-file:pyproject.toml": 1}),
    )
    assert not ref_matches(
        query,
        RefEvaluation(ref="main", hits={"grep:old_api": 2, "has-file:pyproject.toml": 0}),
    )


def test_any_ors_positives_and_negations_still_veto() -> None:
    query = SweepQuery(
        greps=("log4j",),
        has_files=("pom.xml",),
        not_greps=("safe_version",),
        paths=(),
        lacks_files=("blocked.txt",),
        any_mode=True,
        refs=RefSelector(),
    )

    assert ref_matches(
        query,
        RefEvaluation(
            ref="main",
            hits={
                "grep:log4j": 0,
                "has-file:pom.xml": 1,
                "not-grep:safe_version": 0,
                "lacks-file:blocked.txt": 0,
            },
        ),
    )
    assert not ref_matches(
        query,
        RefEvaluation(
            ref="main",
            hits={
                "grep:log4j": 2,
                "has-file:pom.xml": 0,
                "not-grep:safe_version": 1,
                "lacks-file:blocked.txt": 0,
            },
        ),
    )
    assert not ref_matches(
        query,
        RefEvaluation(
            ref="main",
            hits={
                "grep:log4j": 2,
                "has-file:pom.xml": 0,
                "not-grep:safe_version": 0,
                "lacks-file:blocked.txt": 1,
            },
        ),
    )


def test_negation_only_query_matches_clean_repo() -> None:
    query = SweepQuery(
        greps=(),
        has_files=(),
        not_greps=("old_api",),
        paths=(),
        lacks_files=("setup.py",),
        any_mode=True,
        refs=RefSelector(),
    )

    assert ref_matches(
        query,
        RefEvaluation(ref="main", hits={"not-grep:old_api": 0, "lacks-file:setup.py": 0}),
    )
    assert not ref_matches(
        query,
        RefEvaluation(ref="main", hits={"not-grep:old_api": 0, "lacks-file:setup.py": 1}),
    )


def test_zero_predicates_is_invalid() -> None:
    query = SweepQuery(
        greps=(),
        not_greps=(),
        paths=(),
        has_files=(),
        lacks_files=(),
        any_mode=False,
        refs=RefSelector(),
    )

    with pytest.raises(ValueError, match="requires at least one predicate"):
        query.validate()


def test_path_without_content_predicate_is_invalid() -> None:
    query = SweepQuery(
        greps=(),
        not_greps=(),
        paths=("src/**",),
        has_files=("pyproject.toml",),
        lacks_files=(),
        any_mode=False,
        refs=RefSelector(),
    )

    with pytest.raises(ValueError, match="--has-file"):
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
