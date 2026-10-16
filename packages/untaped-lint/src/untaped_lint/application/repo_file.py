"""The repository's lint file, ``.untaped/lint.yml``: its rules, readers and ignores.

Found by walking up from the working directory to the project root (the
nearest ``.git`` ancestor); the directory holding ``.untaped/`` is the root
its paths are relative to. Version 1 holds ``name:`` (the key of the
repository's own rules), ``exclude:`` (globs left out of the tree on top of
what git ignores), ``linters:`` (each key's ``rules:`` directory),
``readers:`` (which installed plugin reads which files into subjects) and
``ignore:`` (suppressions, as ``lint.ignore``). Every key is spelled as a
plugin name and may not be ``untaped`` or an installed plugin's name, so the
file can never shadow a plugin.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from untaped.sdk import (
    ConfigError,
    UsageError,
    first_validation_error,
    project_root,
    read_structured_file,
)
from untaped_lint.domain.selection import parse_suppression
from untaped_lint.records import RULE_ID

DIRECTORY = ".untaped"
FILE_NAME = "lint.yml"
RELATIVE = f"{DIRECTORY}/{FILE_NAME}"
"""Where the file sits under its root."""
RESERVED = "untaped"
"""Core's prefix: no repository key may take it."""
FIXTURES = "fixtures"
"""The directory under a rules directory that holds its good and bad examples."""

_KEY = re.compile(RULE_ID)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LinterEntry(_Model):
    """One ``linters:`` key: the directory of its rule modules."""

    rules: str

    @model_validator(mode="before")
    @classmethod
    def _no_run(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and "run" in value:
            raise ValueError("run: entries are not supported by this version of untaped-lint")
        return value


class RepoFileModel(_Model):
    """``.untaped/lint.yml``, version 1, as written."""

    version: Literal[1]
    name: str
    exclude: list[str] = []
    linters: dict[str, LinterEntry] = {}
    readers: dict[str, list[str]] = {}
    ignore: list[str] = []


@dataclass(frozen=True)
class RepoFile:
    """The repository's lint file, read and checked; paths resolved against ``root``."""

    path: Path
    root: Path
    name: str
    exclude: tuple[str, ...] = ()
    rules: dict[str, Path] = field(default_factory=dict)
    """Each key's rules directory, absolute."""
    readers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    ignore: tuple[str, ...] = ()

    def left_out(self) -> tuple[str, ...]:
        """The globs the tree leaves out: ``exclude:`` and each rules directory's fixtures."""
        fixtures = (
            f"{directory.relative_to(self.root).as_posix()}/{FIXTURES}/**"
            for directory in self.rules.values()
            if directory.is_relative_to(self.root)
        )
        return (*self.exclude, *fixtures)


def find(start: Path) -> Path | None:
    """The nearest ``.untaped/lint.yml`` from ``start`` up to its project root."""
    start = start.resolve()
    top = project_root(start)
    for folder in (start, *start.parents):
        candidate = folder / DIRECTORY / FILE_NAME
        if candidate.is_file():
            return candidate
        if folder == top:
            break
    return None


def root_of(path: Path) -> Path:
    """The root a repo file's paths are relative to: the directory holding ``.untaped/``."""
    path = path.resolve()
    return path.parent.parent if path.parent.name == DIRECTORY else path.parent


def read(path: Path, *, installed: Iterable[str] = ()) -> RepoFile:
    """Read and check the repo file at ``path``; ``installed`` are the plugin names in use.

    An unreadable or invalid file is a :class:`ConfigError` (exit 1); a key
    that is misspelled, reserved or an installed plugin's name is a
    :class:`UsageError` (exit 2), before anything is composed.
    """
    raw = read_structured_file(path)
    try:
        model = RepoFileModel.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"{path} is invalid: {first_validation_error(exc)}", category="invalid"
        ) from None
    held = set(installed)
    for key in (model.name, *model.linters):
        check_key(key, path, held)
    for each in model.ignore:
        parse_suppression(each, option=f"{path} ignore:")
    root = root_of(path)
    return RepoFile(
        path=path.resolve(),
        root=root,
        name=model.name,
        exclude=tuple(model.exclude),
        rules={key: (root / entry.rules).resolve() for key, entry in model.linters.items()},
        readers={key: tuple(globs) for key, globs in model.readers.items()},
        ignore=tuple(model.ignore),
    )


def check_key(key: str, path: Path, held: set[str]) -> None:
    """Refuse a key misspelled, reserved, or held by an installed plugin (``held``)."""
    if not _KEY.match(key):
        raise UsageError(
            f"{path}: {key!r} is not a key: lowercase words joined by single hyphens, "
            "as a plugin name"
        )
    if key == RESERVED:
        raise UsageError(
            f"{path}: {key!r} is core's prefix; name the repository's rules another key"
        )
    if key in held:
        raise UsageError(
            f"{path}: {key!r} is the name of an installed plugin; name the repository's rules "
            "another key"
        )
