"""Apply documents as the documents loader takes them: one entry per YAML document.

A text is split with the same reader ``apply`` uses
(:func:`~untaped_awx.infrastructure.resources.yaml_io.read_resource_documents`),
so a document lint reads is one ``apply`` would read. Errors drop the file
label ``apply`` puts first: a finding already carries its location.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from untaped.sdk import ConfigError
from untaped_awx.domain import Resource
from untaped_awx.infrastructure.resources.yaml_io import read_resource_documents, resource_paths

NOT_TEXT = "not UTF-8 text"
"""The error of a file that does not decode: one unreadable document, at index 1."""


def documents_of(
    texts: Iterable[tuple[str, str | None]],
) -> Iterator[tuple[str, int, Resource | str]]:
    """``(label, index, resource or error)`` per document of each ``(label, text)``.

    A ``None`` text did not decode.
    """
    for label, text in texts:
        if text is None:
            yield label, 1, NOT_TEXT
            continue
        for index, document in read_resource_documents(text, source=label):
            yield label, index, _unlabelled(document, label)


def file_texts(paths: Iterable[Path]) -> Iterator[tuple[str, str | None]]:
    """``(label, text)`` for each file ``paths`` name, a directory read as ``apply`` reads it.

    The label is the path as given, joined to the file under a directory. A
    path that does not exist, or a directory without YAML, is the
    ``not_found`` error ``apply`` raises; a file that cannot be read, an
    ``invalid`` one.
    """
    for given in paths:
        for path in resource_paths(given):
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ConfigError(f"cannot read {path}: {exc}", category="invalid") from exc
            try:
                yield str(path), data.decode("utf-8")
            except UnicodeDecodeError:
                yield str(path), None


def _unlabelled(document: Resource | str, label: str) -> Resource | str:
    if isinstance(document, Resource):
        return document
    for prefix, replacement in (
        (f"invalid YAML in {label}: ", "invalid YAML: "),
        (f"{label}: ", ""),
    ):
        if document.startswith(prefix):
            return replacement + document.removeprefix(prefix)
    return document


__all__ = ["NOT_TEXT", "documents_of", "file_texts"]
