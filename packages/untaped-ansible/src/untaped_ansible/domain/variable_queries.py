"""Which dependents use a variable: the reached rows joined with the variable facts.

Pure over :class:`~untaped_ansible.domain.reach.ReachedNode` rows and the
:class:`~untaped_ansible.domain.payloads.CommitFacts` read at their commits.
Facts are read at the commit each row's repo was indexed at (``shas``), so a
row never mixes the graph of one commit with the files of another.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from untaped.sdk import Record
from untaped_ansible.domain.identity import repo_key
from untaped_ansible.domain.payloads import CommitFacts
from untaped_ansible.domain.reach import ReachedNode
from untaped_ansible.domain.variables import How, VariableFact


class Dependent(BaseModel):
    """The dependent a hit was found in: its repo and ref, and its path to the role."""

    model_config = ConfigDict(frozen=True)

    repo: str
    ref: str | None
    path: list[str]


class VariableUse(Record):
    """Where one variable is defined, set or read in one repo at one ref.

    Kind-less until ``vars where`` registers ``ansible.variable_use``.
    ``key_path`` is the YAML key path as segments (``int`` for list items),
    empty for a read in a template file.
    """

    variable: str
    repo: str
    ref: str | None
    file: str
    line: int
    key_path: list[str | int]
    how: How


class VariableUseHit(VariableUse, kind="ansible.variable_use_hit"):
    """A :class:`VariableUse` in a dependent of the role ``impact --uses`` was asked about."""

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "ref", "file", "line", "key_path", "how")

    dependent: Dependent


def dependent_uses(
    reached: Iterable[ReachedNode], facts: CommitFacts, names: Collection[str]
) -> list[VariableUseHit]:
    """One row per fact naming one of ``names`` in each reached dependent.

    A dependent reached from several root refs, or held by two sources at
    two commits, gives each fact once. Rows are sorted by repo, ref, file
    and line.
    """
    wanted = set(names)
    hits: dict[tuple[str, str | None, str, int, str, str, How], VariableUseHit] = {}
    for node in reached:
        if node.repo is None:
            continue
        for sha in sorted(node.shas):
            for fact in facts.facts.get((node.repo, sha), ()):
                if fact.name not in wanted:
                    continue
                key = (
                    repo_key(node.repo),
                    node.ref,
                    fact.file,
                    fact.line,
                    json.dumps(fact.key_path),
                    fact.name,
                    fact.how,
                )
                hits.setdefault(key, _hit(node, fact))
    return [hits[key] for key in sorted(hits, key=_sort_key)]


def unanalysed(reached: Iterable[ReachedNode], facts: CommitFacts) -> list[str]:
    """``repo@ref`` of each reached dependent with a commit no analysis covers, sorted."""
    return sorted(
        {
            _label(node)
            for node in reached
            if node.repo is not None
            and any((node.repo, sha) in facts.unanalysed for sha in node.shas)
        }
    )


def skipped_files(reached: Iterable[ReachedNode], facts: CommitFacts) -> list[str]:
    """``repo@ref path: reason`` of each file the analysis skipped in a reached dependent."""
    lines: dict[str, None] = {}
    for node in reached:
        if node.repo is None:
            continue
        for sha in sorted(node.shas):
            for path, reason in facts.skipped.get((node.repo, sha), ()):
                lines.setdefault(f"{_label(node)} {path}: {reason}", None)
    return sorted(lines)


def defines_any(facts: Iterable[VariableFact], names: Collection[str]) -> bool:
    """Whether any of ``facts`` defines one of ``names`` (defaults, vars, argument_specs)."""
    wanted = set(names)
    return any(fact.how == "defines" and fact.name in wanted for fact in facts)


def render_key_path(key_path: list[str | int]) -> str:
    """``key_path`` dotted, ``[n]`` for list items, a segment with ``.``, ``"`` or ``[`` quoted.

    ``[3, "ansible.builtin.set_fact", "port"]`` → ``[3]."ansible.builtin.set_fact".port``.
    """
    rendered = ""
    for segment in key_path:
        if isinstance(segment, int):
            rendered += f"[{segment}]"
            continue
        text = segment
        if any(character in segment for character in '."[') or not segment:
            text = '"' + segment.replace("\\", "\\\\").replace('"', '\\"') + '"'
        rendered += f".{text}" if rendered else text
    return rendered


def _hit(node: ReachedNode, fact: VariableFact) -> VariableUseHit:
    assert node.repo is not None
    return VariableUseHit(
        variable=fact.name,
        repo=node.repo,
        ref=node.ref,
        file=fact.file,
        line=fact.line,
        key_path=fact.key_path,
        how=fact.how,
        dependent=Dependent(repo=node.repo, ref=node.ref, path=node.path),
    )


def _sort_key(
    key: tuple[str, str | None, str, int, str, str, How],
) -> tuple[str, str, str, int, str, str, str]:
    repo, ref, file, line, path, name, how = key
    return (repo, ref or "", file, line, path, name, how)


def _label(node: ReachedNode) -> str:
    return f"{node.repo}@{node.ref}" if node.ref else str(node.repo)


__all__ = [
    "Dependent",
    "VariableUse",
    "VariableUseHit",
    "defines_any",
    "dependent_uses",
    "render_key_path",
    "skipped_files",
    "unanalysed",
]
