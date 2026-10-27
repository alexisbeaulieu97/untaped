"""Which dependents use a variable: the reached rows joined with the variable facts.

Pure over :class:`~untaped_ansible.domain.reach.Reach` hits and the
:class:`~untaped_ansible.domain.variable_models.CommitFacts` read at their commits.
Facts are read at the commit each hit's repo was indexed at (``shas``), so a
row never mixes the graph of one commit with the files of another.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable

from untaped_ansible.domain.identity import repo_key
from untaped_ansible.domain.reach import Reach
from untaped_ansible.domain.variable_models import CommitFacts, How, VariableFact
from untaped_ansible.records import FoundIn, ReachedRow, VariableUseHit


def dependent_uses(
    reached: Iterable[Reach], facts: CommitFacts, names: Collection[str]
) -> list[VariableUseHit]:
    """One row per fact naming one of ``names`` in each reached dependent.

    A dependent reached from several root refs, or held by two sources at
    two commits, gives each fact once. Rows are sorted by repo, ref, file
    and line.
    """
    wanted = set(names)
    hits: dict[tuple[str, str | None, str, int, str, str, How], VariableUseHit] = {}
    for hit in reached:
        node = hit.row
        if node.repo is None:
            continue
        for sha in sorted(hit.shas):
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


def unanalysed(reached: Iterable[Reach], facts: CommitFacts) -> list[str]:
    """``repo@ref`` of each reached dependent with a commit no analysis covers, sorted.

    A dependent with no commit at all (its edges carry none) is unanalysed too.
    """
    return sorted(
        {
            _label(hit.row)
            for hit in reached
            if hit.row.repo is not None
            and (not hit.shas or any((hit.row.repo, sha) in facts.unanalysed for sha in hit.shas))
        }
    )


def skipped_files(reached: Iterable[Reach], facts: CommitFacts) -> list[str]:
    """``repo@ref path: reason`` of each file the analysis skipped in a reached dependent."""
    lines: dict[str, None] = {}
    for hit in reached:
        node = hit.row
        if node.repo is None:
            continue
        for sha in sorted(hit.shas):
            for path, reason in facts.skipped.get((node.repo, sha), ()):
                lines.setdefault(f"{_label(node)} {path}: {reason}", None)
    return sorted(lines)


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


def _hit(node: ReachedRow, fact: VariableFact) -> VariableUseHit:
    assert node.repo is not None
    return VariableUseHit(
        variable=fact.name,
        repo=node.repo,
        ref=node.ref,
        file=fact.file,
        line=fact.line,
        key_path=fact.key_path,
        how=fact.how,
        dependent=FoundIn(repo=node.repo, ref=node.ref, path=node.path),
    )


def _sort_key(
    key: tuple[str, str | None, str, int, str, str, How],
) -> tuple[str, str, str, int, str, str, str]:
    repo, ref, file, line, path, name, how = key
    return (repo, ref or "", file, line, path, name, how)


def _label(node: ReachedRow) -> str:
    return f"{node.repo}@{node.ref}" if node.ref else str(node.repo)


__all__ = [
    "dependent_uses",
    "render_key_path",
    "skipped_files",
    "unanalysed",
]
