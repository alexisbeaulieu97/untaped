"""The records ansible's commands write; each one's kind is ``ansible.`` and its class name.

Models only: the domain code that fills them imports them. A model's fields,
in order, are its rows' keys on the wire.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Column, FreshnessRecord, OutcomeRecord, Record, Status, UtcTimestamp
from untaped_ansible.domain.graph import StopReason
from untaped_ansible.domain.variable_models import How


class Source(Record):
    """A saved source (``source list``, ``source get``): the GitHub scope a refresh scans.

    ``ref_scan_default`` is ``null`` when the source leaves it to the setting
    of the same name.
    """

    name: Annotated[str, Column]
    orgs: Annotated[list[str], Column]
    teams: Annotated[list[str], Column]
    repos: Annotated[list[str], Column]
    dependency_paths: Annotated[list[str], Column]
    ref_kinds: Annotated[list[str], Column]
    ref_patterns: Annotated[list[str], Column]
    ref_scan_default: Annotated[Literal["all", "default_branch"] | None, Column] = None


class SourceAlias(Record):
    """A role or Galaxy name mapped to the GitHub repo it stands for (``source-alias list``)."""

    alias: str
    repo: str


class SourceAliasOutcome(OutcomeRecord):
    """What ``source-alias set`` or ``remove`` did to one alias (no ``repo`` for a damaged one)."""

    alias: str
    repo: str | None = None


class SourceOutcome(OutcomeRecord):
    """What ``source set``, ``patch`` or ``remove`` did to one source; ``changes`` says what."""

    name: str
    changes: list[str] = Field(default_factory=list)


class SourceStatus(FreshnessRecord):
    """One row of ``source status``.

    Its freshness (``refreshed_at``, ``stale``, ``partial``, ``retry_at``…)
    is the cache's; ``scanned_at`` and the counts are when the index was
    last written and what it holds.
    """

    source: Annotated[str, Column]
    source_key: str
    state: Annotated[
        Literal["fresh", "stale", "not_refreshed", "missing_source"],
        Column,
        Status(warning=("stale",)),
    ] = Field(
        description=(
            "stale when past ansible.caches.source_data.max_age_seconds, made by another untaped "
            "version, or after a failed or partial refresh; not_refreshed when never "
            "refreshed; fresh otherwise, with zero counts for an empty source. "
            "missing_source is reserved."
        )
    )
    configured: bool
    scanned_at: UtcTimestamp | None = None
    repos: Annotated[int, Column] = 0
    refs: Annotated[int, Column] = 0
    edges: int = 0
    refreshed_at: Annotated[UtcTimestamp | None, Column]


class DependencyMatch(Record):
    """One wanted repository reached from one root (``find``).

    A root read from a pipe record carries that record's kind, ``id`` and
    ``name`` as ``input_kind``, ``input_id`` and ``input_name``.
    """

    root_repo: Annotated[str, Column]
    root_ref: Annotated[str | None, Column] = Field(
        description=(
            "The ref the root was read at (its default branch when none was given); null "
            "when it has no known ref."
        )
    )
    repo: Annotated[str, Column]
    declared_ref: Annotated[str | None, Column] = Field(
        description="The ref as the edge that reached the repo declares it, verbatim."
    )
    declared_in: str | None = Field(description="The dependency file that declares that edge.")
    path: Annotated[list[str], Column] = Field(
        description="The shortest path from the root to the repo."
    )
    input_kind: str | None = Field(
        default=None, description="The kind of the pipe record the root came from, else null."
    )
    input_id: int | str | None = Field(default=None, description="That record's id, else null.")
    input_name: str | None = Field(default=None, description="That record's name, else null.")


class ReachedRow(Record):
    """One repository reached from a root in one direction: a base, see its two records.

    ``path`` reads in dependency order (each label requires the next).
    ``root_ref`` is the ref of the root it was reached from; ``stopped`` says
    why the walk did not read the node's own dependencies.
    """

    repo: Annotated[str | None, Column]
    ref: Annotated[str | None, Column] = Field(
        description="The ref reached; an unpinned dependency reaches its default branch."
    )
    unresolved: str | None = Field(
        description="The declared name of a dependency that names no GitHub repo, else null."
    )
    declared_ref: str | None = Field(
        description="The ref as the edge that reached the repo declares it, verbatim."
    )
    declared_in: Annotated[str | None, Column] = Field(
        description="The dependency file that declares that edge."
    )
    depth: int = Field(description="Hops from the root.")
    path: Annotated[list[str], Column] = Field(
        description=(
            "The shortest path, in dependency order. A table cuts it to its first and last "
            "hop; other formats keep it whole."
        )
    )
    root_ref: str | None = Field(description="The root's ref this row was reached from.")
    stopped: Annotated[StopReason | None, Column] = Field(
        description=(
            "depth when --depth ran out before this repo's own edges were read, not_cached "
            "when its ref is not in the source's cache, else null. Either way more repos may "
            "lie beyond it."
        )
    )


class Dependency(ReachedRow):
    """A repository the root depends on, directly or not (``deps``); the path starts at the root."""


class Dependent(ReachedRow):
    """A repository that depends on the root, directly or not (``impact``); the path ends there."""


class FoundIn(BaseModel):
    """The dependent a hit was found in: its repo and ref, and its path to the role."""

    model_config = ConfigDict(frozen=True)

    repo: str
    ref: str | None
    path: list[str]


class VariableUse(Record):
    """Where one variable is defined, set or read in one repo at one ref.

    A base: no command prints it as it is.
    ``key_path`` is the YAML key path as segments (``int`` for list items),
    empty for a read in a template file.
    """

    variable: str
    repo: str
    ref: str | None
    file: str
    line: int
    key_path: list[str | int] = Field(
        description="YAML key segments (int for list items); empty for a template file's read."
    )
    how: How = Field(
        description="Whether this place defines, sets or reads the variable (see impact uses)."
    )


class VariableUseHit(VariableUse):
    """A :class:`VariableUse` in a dependent of the role ``impact uses`` was asked about."""

    dependent: FoundIn


__all__ = [
    "Dependency",
    "DependencyMatch",
    "Dependent",
    "FoundIn",
    "ReachedRow",
    "Source",
    "SourceAlias",
    "SourceAliasOutcome",
    "SourceOutcome",
    "SourceStatus",
    "VariableUse",
    "VariableUseHit",
]
