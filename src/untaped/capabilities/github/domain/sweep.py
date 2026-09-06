"""Pure domain model for sweep predicates and ref selection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

RefProfile = Literal["default", "branches", "tags", "all"]


@dataclass(frozen=True)
class RefSelector:
    """Selected ref profile plus explicit ref globs."""

    profile: RefProfile = "default"
    globs: tuple[str, ...] = ()

    def beyond_default(self) -> bool:
        return self.profile != "default" or bool(self.globs)


@dataclass(frozen=True)
class SweepQuery:
    """Sweep predicates and repo-level boolean mode."""

    greps: tuple[str, ...] = ()
    not_greps: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    has_files: tuple[str, ...] = ()
    lacks_files: tuple[str, ...] = ()
    any_mode: bool = False
    ignore_case: bool = False
    fixed_strings: bool = False
    word_regexp: bool = False
    refs: RefSelector = field(default_factory=RefSelector)

    def labels(self) -> tuple[str, ...]:
        return (
            *(f"grep:{pattern}" for pattern in self.greps),
            *(f"not-grep:{pattern}" for pattern in self.not_greps),
            *(f"has-file:{glob}" for glob in self.has_files),
            *(f"lacks-file:{glob}" for glob in self.lacks_files),
        )

    def validate(self) -> None:
        if self.paths and not self.greps and not self.not_greps:
            raise ValueError("--path requires --grep or --not-grep; use --has-file for presence")
        if not self.labels():
            raise ValueError("sweep requires at least one predicate")


@dataclass(frozen=True)
class RefEvaluation:
    """Predicate hit counts for one selected ref."""

    ref: str
    hits: Mapping[str, int]


@dataclass(frozen=True)
class RepoSweepOutcome:
    """Aggregated sweep result for one repository."""

    full_name: str
    clone_url: str | None
    matched: bool
    refs_matched: tuple[str, ...]
    hits: Mapping[str, int]
    owners: tuple[str, ...]
    synced_at: str | None


def ref_matches(query: SweepQuery, evaluation: RefEvaluation) -> bool:
    """Return whether one ref satisfies the sweep query."""
    query.validate()
    positive_labels = (
        *(f"grep:{pattern}" for pattern in query.greps),
        *(f"has-file:{glob}" for glob in query.has_files),
    )
    negated_labels = (
        *(f"not-grep:{pattern}" for pattern in query.not_greps),
        *(f"lacks-file:{glob}" for glob in query.lacks_files),
    )

    if not all(evaluation.hits.get(label, 0) == 0 for label in negated_labels):
        return False
    if not positive_labels:
        return True
    positive_hits = [evaluation.hits.get(label, 0) > 0 for label in positive_labels]
    return any(positive_hits) if query.any_mode else all(positive_hits)


def profile_join(stored: RefProfile, requested: RefProfile) -> RefProfile:
    """Return the widening join of two corpus fetch profiles."""
    if stored == requested:
        return stored
    if stored == "all" or requested == "all":
        return "all"
    if stored == "default":
        return requested
    if requested == "default":
        return stored
    return "all"
