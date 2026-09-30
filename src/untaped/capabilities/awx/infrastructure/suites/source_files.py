"""Suites and template specs read at one commit, for ``awx test … --source-ref REF``.

:class:`GitSuiteFiles` is the loader's :class:`Filesystem` at a pinned
commit: it finds the suites under the paths given (as the working-tree
discovery does) and names each ``REF:PATH``, so every error names the file
at the ref. :func:`template_specs` reads the ``JobTemplate`` and
``WorkflowJobTemplate`` documents anywhere under ``.untaped/awx/`` at that
commit (suites, vars files and other kinds are skipped). Both read through
:class:`GitSource`, the reader ``awx apply --source-ref`` uses, which reads
each file once.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from untaped.capabilities.awx.domain import Resource
from untaped.capabilities.awx.domain.temporary_set import TEMPLATE_KINDS
from untaped.capabilities.awx.infrastructure.git_source import GitSource
from untaped.capabilities.awx.infrastructure.suites.filesystem import (
    is_hidden,
    is_suite_text,
    kind_marker,
)
from untaped.capabilities.awx.infrastructure.yaml_io import read_resource_files_at
from untaped.capability_api import ConfigError, ErrorCategory

SPEC_ROOT = Path(".untaped/awx")
"""Where a repository keeps its specs and suites, at its root."""

_SPEC_MARKER = kind_marker(TEMPLATE_KINDS)
"""A ``kind:`` of a spec, block or flow style, with a trailing comment or not."""


class GitSuiteFiles:
    """Suite files at ``source``'s commit; each is named by a ``REF:PATH`` path."""

    def __init__(self, source: GitSource) -> None:
        self._source = source
        self._files: dict[Path, str] = {}

    def suites(self, paths: Iterable[Path]) -> list[Path]:
        """The files ``paths`` name, and the suites under the directories they name."""
        found: list[Path] = []
        for path in paths:
            named = self._source.repo_path(path)
            listed = self._source.files(path)
            if listed != [named]:
                listed = [
                    rel
                    for rel in listed
                    if not is_hidden(PurePosixPath(rel).relative_to(named or "."))
                    and is_suite_text(self._source.read_text(rel))
                ]
                if not listed:
                    raise ConfigError(
                        f"no test suites under {named or '.'} at {self._source.ref}",
                        category="not_found",
                        system="git",
                    )
            found.extend(self._label(rel) for rel in listed)
        return list(dict.fromkeys(found))

    def read_text(self, path: Path) -> str:
        return self._source.read_text(self._files[path])

    def _label(self, rel: str) -> Path:
        path = Path(self._source.label(rel))
        self._files[path] = rel
        return path


def template_specs(source: GitSource) -> list[tuple[str, Resource]]:
    """Every job template and workflow spec under :data:`SPEC_ROOT` at the commit.

    Each comes with the ``REF:PATH`` it was read from; none when the commit
    has no such directory. Suites are skipped first: a ``!ref {kind:
    JobTemplate, …}`` in one would look like a spec.
    """

    def skip(rel: str, text: str) -> bool:
        hidden = is_hidden(PurePosixPath(rel).relative_to(SPEC_ROOT.as_posix()))
        return hidden or is_suite_text(text) or not _SPEC_MARKER.search(text)

    try:
        found = read_resource_files_at(source, source.root / SPEC_ROOT, skip=skip)
    except ConfigError as exc:
        if exc.category == ErrorCategory.NOT_FOUND:
            return []
        raise
    return [(label, doc) for label, doc in found if doc.kind in TEMPLATE_KINDS]
