"""The repository's lint file, ``.untaped/lint.yml``: its rules, readers and ignores.

Found by walking up from the working directory to the checkout root (the
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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from untaped.sdk import (
    ConfigError,
    UsageError,
    checkout_root,
    first_validation_error,
    plugin_of,
    read_structured_file,
)
from untaped_lint.domain.selection import parse_suppression
from untaped_lint.records import RULE_ID
from untaped_lint.settings import LintSettings


def lint_file(root: Path) -> Path:
    """The lint file under ``root``: ``<root>/.untaped/lint.yml``, lint's name there."""
    return plugin_of(LintSettings).committed("lint.yml", root=root)


def rules_dir(root: Path) -> Path:
    """The default rules directory under ``root``: ``<root>/.untaped/lint/``."""
    return plugin_of(LintSettings).committed("lint", root=root)


RESERVED = "untaped"
"""Core's prefix: no repository key may take it."""
FIXTURES = "fixtures"
"""The directory under a rules directory that holds its good and bad examples."""

_KEY = re.compile(RULE_ID)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LinterEntry(_Model):
    """One ``linters:`` key: the directory of its rule modules."""

    rules: str = Field(description="The directory of the key's rule modules: each *.py in it.")

    @model_validator(mode="before")
    @classmethod
    def _no_run(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and "run" in value:
            raise ValueError("run: entries are not supported by this version of untaped-lint")
        return value


class LintFile(_Model):
    """A repository's `.untaped/lint.yml`: its keys, where it is found, and what loading runs.

    Read before writing or editing `.untaped/lint.yml`.

    ```yaml
    version: 1
    name: house                  # the key of the repository's own rules: house.no-print
    exclude: ["vendor/**"]       # left out of the files, on top of what git ignores
    linters:
      house:
        rules: .untaped/lint     # a directory: each *.py directly in it
    readers:
      awx: ["specs/**/*.yml"]    # an installed plugin reads these files into its records
    ignore:
      - house.no-print:scripts/**
    ```

    - `untaped lint` finds the file walking up from the current directory to the
      checkout root (the nearest directory holding `.git`). `--config PATH` reads
      another file and lints the current directory with its rules.
    - `name:` and each `linters:` key are spelled like a plugin name and are never
      `untaped` or an installed plugin's name; such a key exits 2. A `readers:`
      key names an installed plugin.
    - The files linted are what git tracks plus untracked files git doesn't
      ignore, so a new file is linted before `git add`; outside a checkout, every
      file under the root. `fixtures/` under a rules directory is never linted.
    - A `readers:` key no installed plugin fills exits 4.

    ## What loading the rules runs

    A rule module is Python that `untaped lint` imports in its own process, as
    pytest imports a `conftest.py`: no sandbox and no confirmation, so a
    repository's `.untaped/lint/` runs as you when you lint that checkout. A
    module may import only the standard library, `untaped_lint.api` and an
    installed plugin's `untaped_<plugin>.api`, because it runs in untaped's
    environment, not the repository's (`lint.rule-imports` reports anything
    else). Importing a plugin that isn't installed exits 4 naming the extra to
    install. A rule that needs another library belongs in a plugin
    (`untaped plugin new`).
    """

    version: Literal[1] = Field(description="The lint file's format: 1.")
    name: str = Field(description="The key of the repository's own rules: `house.no-print`.")
    exclude: list[str] = Field(
        default_factory=list, description="Globs left out of the files, beyond what git ignores."
    )
    linters: dict[str, LinterEntry] = Field(
        default_factory=dict, description="Each key's rules directory, relative to the root."
    )
    readers: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Which installed plugin reads which files (globs) into its records.",
    )
    ignore: list[str] = Field(
        default_factory=list, description="Suppressions for every run here, as lint.ignore."
    )


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
        )
        return (*self.exclude, *fixtures)


def find(start: Path) -> Path | None:
    """The nearest ``.untaped/lint.yml`` from ``start`` up to its checkout root."""
    start = start.resolve()
    top = checkout_root(start) or start
    for folder in (start, *start.parents):
        candidate = lint_file(folder)
        if candidate.is_file():
            return candidate
        if folder == top:
            break
    return None


def root_of(path: Path) -> Path:
    """The root a repo file's paths are relative to: the directory holding ``.untaped/``."""
    path = path.resolve()
    folder = lint_file(Path()).parent.name
    return path.parent.parent if path.parent.name == folder else path.parent


def read(path: Path, *, installed: Iterable[str] = ()) -> RepoFile:
    """Read and check the repo file at ``path``; ``installed`` are the plugin names in use.

    An unreadable or invalid file (a ``rules:`` directory outside the root,
    a malformed ``ignore:`` entry) is a :class:`ConfigError` (exit 1); a key
    that is misspelled, reserved or an installed plugin's name is a
    :class:`UsageError` (exit 2), before anything is composed.
    """
    raw = read_structured_file(path)
    try:
        model = LintFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"{path} is invalid: {first_validation_error(exc)}", category="invalid"
        ) from None
    held = set(installed)
    for key in (model.name, *model.linters):
        check_key(key, path, held)
    for key in model.readers:
        _check_spelling(key, path)
    for each in model.ignore:
        try:
            parse_suppression(each, option=f"{path} ignore:")
        except UsageError as exc:
            raise ConfigError(str(exc), category="invalid") from None
    root = root_of(path)
    rules = {key: (root / entry.rules).resolve() for key, entry in model.linters.items()}
    for key, directory in rules.items():
        if not directory.is_relative_to(root):
            raise ConfigError(
                f"{path}: linters.{key}.rules: {directory} is outside {root}; a repository's "
                "rule modules live in the repository",
                category="invalid",
            )
    return RepoFile(
        path=path.resolve(),
        root=root,
        name=model.name,
        exclude=tuple(model.exclude),
        rules=rules,
        readers={key: tuple(globs) for key, globs in model.readers.items()},
        ignore=tuple(model.ignore),
    )


def check_key(key: str, path: Path, held: set[str]) -> None:
    """Refuse a key misspelled, reserved, or held by an installed plugin (``held``)."""
    _check_spelling(key, path)
    if key == RESERVED:
        raise UsageError(
            f"{path}: {key!r} is core's prefix; name the repository's rules another key"
        )
    if key in held:
        raise UsageError(
            f"{path}: {key!r} is the name of an installed plugin; name the repository's rules "
            "another key"
        )


def _check_spelling(key: str, path: Path) -> None:
    if not _KEY.match(key):
        raise UsageError(
            f"{path}: {key!r} is not a key: lowercase words joined by single hyphens, "
            "as a plugin name"
        )
