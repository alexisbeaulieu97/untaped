"""Suites and template specs read at one commit, for ``awx test … --source-ref REF``.

:class:`GitSuiteFiles` is the loader's :class:`Filesystem` at a pinned
commit: it finds the suites under the paths given (as the working-tree
discovery does) and names each ``REF:PATH``, so every error names the file
at the ref. :func:`template_specs` reads the ``JobTemplate`` and
``WorkflowJobTemplate`` documents anywhere under ``.untaped/awx/`` at that
commit (suites, vars files and other kinds are skipped). Both read through
:class:`GitSource`, the reader ``awx apply --source-ref`` uses.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from untaped.capabilities.awx.domain import Resource
from untaped.capabilities.awx.domain.temporary_set import TEMPLATE_KINDS
from untaped.capabilities.awx.infrastructure.git_source import GitSource
from untaped.capabilities.awx.infrastructure.suites.filesystem import is_suite_text
from untaped.capabilities.awx.infrastructure.yaml_io import read_resource_text
from untaped.capability_api import ConfigError, ErrorCategory

SPEC_ROOT = Path(".untaped/awx")
"""Where a repository keeps its specs and suites, at its root."""

_SPEC_MARKER = re.compile(rf"^kind:\s*[\"']?(?:{'|'.join(TEMPLATE_KINDS)})[\"']?\s*$", re.MULTILINE)


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
                    if not _hidden(rel, under=named) and is_suite_text(self._read(rel))
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
        return self._read(self._files[path])

    def _label(self, rel: str) -> Path:
        path = Path(self._source.label(rel))
        self._files[path] = rel
        return path

    def _read(self, rel: str) -> str:
        return self._source.read_text(rel)


def template_specs(source: GitSource) -> list[tuple[str, Resource]]:
    """Every job template and workflow spec under :data:`SPEC_ROOT` at the commit.

    Each comes with the ``REF:PATH`` it was read from; none when the commit
    has no such directory.
    """
    root = source.root / SPEC_ROOT
    try:
        listed = source.files(root)
    except ConfigError as exc:
        if exc.category == ErrorCategory.NOT_FOUND:
            return []
        raise
    specs: list[tuple[str, Resource]] = []
    for rel in listed:
        if _hidden(rel, under=SPEC_ROOT.as_posix()):
            continue
        text = source.read_text(rel)
        if is_suite_text(text) or not _SPEC_MARKER.search(text):
            continue
        label = source.label(rel)
        specs.extend(
            (label, doc)
            for doc in read_resource_text(text, source=label)
            if doc.kind in TEMPLATE_KINDS
        )
    return specs


def _hidden(rel: str, *, under: str) -> bool:
    """Whether a part of ``rel`` below the directory ``under`` is hidden."""
    parts = PurePosixPath(rel).relative_to(under).parts if under else PurePosixPath(rel).parts
    return any(part.startswith(".") for part in parts)
