"""The package file (``.untaped/dotfiles/<package>.yml``): its model and every rule it follows.

A package is read from a source, never written. Its ``version`` is the file
format's own stamp: an older untaped refuses a newer format with one line,
and a newer untaped keeps reading older formats. Every rule is checked when
the package loads, so a bad file is one ``error`` row and the other packages
still load.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from untaped.sdk import first_validation_error
from untaped_dotfiles.errors import PackageError
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.settings import Os
from untaped_values.api import Input, InputError, placeholders

#: The package format this untaped reads and writes in its guide.
PACKAGE_VERSION = 1

Mode = Literal["link", "copy", "merge"]
MergeFormat = Literal["json", "yaml", "toml"]
InputType = Literal["str", "int", "float", "bool", "choice"]

#: Sources, packages, items and steps: one segment of a config key each, so no dot.
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
#: Inputs read as identifiers inside ``{{ name }}``.
INPUT_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_FORMATS: dict[str, MergeFormat] = {
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
}


def _named(value: Mapping[str, Any], what: str, pattern: re.Pattern[str] = NAME) -> None:
    for name in value:
        if not pattern.match(name):
            rule = (
                "letters, digits and '_'"
                if pattern is INPUT_NAME
                else "letters, digits, '_' or '-'"
            )
            raise ValueError(f"{what} name {name!r} must be lowercase {rule}")


def _inside(value: str, what: str) -> str:
    cleaned = value.strip().replace("\\", "/").strip("/")
    if not cleaned or value.startswith(("/", "~")) or ":" in value.split("/", 1)[0]:
        raise ValueError(f"{what} must be a path relative to the source's root")
    if any(part in ("", ".", "..") for part in cleaned.split("/")):
        raise ValueError(f"{what} must not contain '.' or '..' segments")
    return cleaned


def _destination(value: str, what: str) -> str:
    """``value`` when it starts with ``~/``, ``/`` or a placeholder, with no ``..`` segment."""
    if not value.startswith(("~/", "/", "{{")):
        raise ValueError(f"{what} must start with '~/' or '/'")
    if any(part == ".." for part in value.replace("\\", "/").split("/")):
        raise ValueError(f"{what} must not contain '..' segments")
    return value


class PackageInput(Input):
    """A value a package needs from the machine: a scalar, never a list or a mapping.

    Top-level inputs are asked once and kept; a step's are asked each time
    it runs. A `secret` input has no `default` and reaches a step only on its
    `stdin`.
    """

    type: InputType = Field("str", description="How a given value is converted.")

    @model_validator(mode="after")
    def _secret_has_no_default(self) -> Self:
        if self.secret and self.default is not None:
            raise ValueError("a secret input has no default")
        return self


class Where(BaseModel):
    """Where an entry applies: the machine's OS and tags (every machine when all are empty)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    os: tuple[Os, ...] = Field((), description="The OSes it applies on; empty: every OS.")
    only: tuple[str, ...] = Field(
        (), description="It applies only on a machine with any of these tags."
    )
    unless: tuple[str, ...] = Field(
        (), description="It doesn't apply on a machine with any of these tags."
    )

    def exclusion(self, os: str, tags: frozenset[str]) -> str | None:
        """Why this doesn't apply on a machine with ``os`` and ``tags``, or ``None``."""
        if self.os and os not in self.os:
            return f"{' or '.join(self.os)} only"
        if self.only and not tags.intersection(self.only):
            return f"needs tag {' or '.join(sorted(self.only))}"
        hit = tags.intersection(self.unless)
        if hit:
            return f"excluded by tag {', '.join(sorted(hit))}"
        return None


class File(Where):
    """A file or folder of the source placed at a target on the machine."""

    path: str = Field(
        description="In the source, relative to its root; a folder is placed per child."
    )
    target: str = Field(description="`~/...` or absolute, after `{{ input }}` substitution.")
    mode: Mode = "link"
    format: MergeFormat | None = Field(
        None, description="`merge` only, when the target's extension doesn't say."
    )

    @field_validator("path")
    @classmethod
    def _path_inside(cls, value: str) -> str:
        return _inside(value, "path")

    @field_validator("target")
    @classmethod
    def _target(cls, value: str) -> str:
        return _destination(value, "target")

    @property
    def merge_format(self) -> MergeFormat | None:
        """``merge`` only: the explicit ``format``, else the one the target's extension names."""
        if self.mode != "merge":
            return None
        if self.format is not None:
            return self.format
        leaf = self.target.rsplit("/", 1)[-1]
        dot = leaf.rfind(".")
        return _FORMATS.get(leaf[dot:].lower()) if dot >= 0 else None

    @model_validator(mode="after")
    def _format(self) -> Self:
        if self.mode == "merge" and self.merge_format is None:
            raise ValueError(
                f"merge target {self.target!r} needs a .json, .yml, .yaml or .toml "
                "extension or a format"
            )
        if self.mode != "merge" and self.format is not None:
            raise ValueError("format applies to merge files only")
        return self


class Check(BaseModel):
    """What tells a step it is done: exactly one of a command, a path or a program."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: tuple[str, ...] | None = Field(None, description="Passes when it exits 0 ...")
    prints: str | None = Field(None, description="... and its stripped stdout is exactly this.")
    path: str | None = Field(None, description="Passes when the path exists (`~/...` or absolute).")
    program: str | None = Field(None, description="Passes when the program is on PATH.")

    @model_validator(mode="after")
    def _one(self) -> Self:
        given = [name for name in ("command", "path", "program") if getattr(self, name)]
        if len(given) != 1:
            raise ValueError("a check has exactly one of command, path or program")
        if self.prints is not None and self.command is None:
            raise ValueError("prints applies to a command check only")
        if self.path is not None:
            _destination(self.path, "check path")
        return self


class Step(Where):
    """A check plus a run (a program, or a script in the source), or a fix for a person."""

    name: str = Field(description="Unique in the item; what status names.")
    description: str = ""
    check: Check | None = Field(None, description="Required unless `kind` is set.")
    run: tuple[str, ...] | None = Field(None, description="argv, no shell; a program on PATH.")
    script: str | None = Field(None, description="Or a file in the source, run as the program ...")
    args: tuple[str, ...] = Field((), description="... with these arguments.")
    stdin: str | None = Field(None, description="The input whose value the program reads.")
    inputs: dict[str, PackageInput] = Field(
        default_factory=dict, description="Asked each time the step runs."
    )
    fix: str | None = Field(None, description="What a person does; required when nothing runs.")
    kind: str | None = Field(None, description="A step kind a plugin provides ...")
    with_: dict[str, JsonValue] = Field(
        default_factory=dict, alias="with", description="... and its fields."
    )

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        _named({value: None}, "step")
        return value

    @field_validator("inputs")
    @classmethod
    def _input_names(cls, value: dict[str, PackageInput]) -> dict[str, PackageInput]:
        _named(value, "input", INPUT_NAME)
        return value

    @field_validator("script")
    @classmethod
    def _script_inside(cls, value: str | None) -> str | None:
        return None if value is None else _inside(value, "script")

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.kind is None:
            if self.check is None:
                raise ValueError(f"step {self.name!r} needs a check")
            if self.with_:
                raise ValueError(f"step {self.name!r}: with applies to a kind only")
        if self.run is not None and self.script is not None:
            raise ValueError(f"step {self.name!r} has run and script; keep one")
        if self.args and self.script is None:
            raise ValueError(f"step {self.name!r}: args go with a script")
        if self.kind is None and self.run is None and self.script is None and self.fix is None:
            raise ValueError(f"step {self.name!r} needs run, script or fix")
        return self

    def texts(self) -> Iterator[tuple[str, str]]:
        """``(field, text)`` for every field that renders ``{{ name }}`` placeholders."""
        if self.check is not None:
            for each in self.check.command or ():
                yield "check.command", each
            if self.check.prints is not None:
                yield "check.prints", self.check.prints
            if self.check.path is not None:
                yield "check.path", self.check.path
        for each in self.run or ():
            yield "run", each
        for each in self.args:
            yield "args", each


class Repo(Where):
    """A working copy a person works in, cloned once when its target is absent."""

    url: str = Field(description="A clone URL, or a name a repo source lists.")
    target: str = Field(description="`~/...` or absolute: where the working copy goes.")
    branch: str | None = Field(None, description="Default: the remote's default branch.")

    @field_validator("target")
    @classmethod
    def _target(cls, value: str) -> str:
        return _destination(value, "repo target")


class Item(Where):
    """A named part of a package: files to place, steps to run, repos to clone."""

    description: str = ""
    files: tuple[File, ...] = ()
    steps: tuple[Step, ...] = Field((), description="Run in order.")
    repos: tuple[Repo, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if not (self.files or self.steps or self.repos):
            raise ValueError("an item needs at least one file, step or repo")
        names = [step.name for step in self.steps]
        twice = sorted({name for name in names if names.count(name) > 1})
        if twice:
            raise ValueError(f"step {twice[0]!r} is named twice")
        return self


class PackageFile(Where):
    """A package: what a source offers a machine, one file `.untaped/dotfiles/<name>.yml`.

    The file's name is the package's. A source offers several packages; a
    machine turns each on or off (`untaped dotfiles add`), and the items of a
    package that is on are on unless the machine turns one off.

    ## Rules, checked when the package loads

    - `version: 1` is required: the file's format. An older untaped refuses
      a newer format and asks you to upgrade; a newer untaped keeps reading
      older ones.
    - Package, item and step names: lowercase letters, digits, `_` and `-`,
      starting with a letter or digit. Input names: lowercase letters, digits
      and `_`, not starting with a digit.
    - An item has at least one file, step or repo.
    - `path` (and a step's `script`) is relative to the source's root and
      stays inside it. `target` (and a repo's `target`, a check's `path`)
      starts with `~/` or `/` after `{{ input }}` substitution and is not the
      home folder or above it. Two files of a package with one target are
      refused; one `path` may go to several targets.
    - `mode` defaults to `link`. `merge` needs a JSON, YAML or TOML target,
      by its extension (`.json`, `.yml`, `.yaml`, `.toml`) or `format`;
      `format` without `merge` is refused. A merge writes the keys the
      source names: mappings (TOML tables) recurse, other values (lists and
      arrays of tables too) replace, and the target's other keys stay, with
      a YAML or TOML target's comments and order. In TOML, a new table
      under dotted keys (`a.b = 1`) is written inline, and a merge that
      would move the target's other keys is refused, writing nothing.
    - `policy` is the author's suggestion: `sync`, `once` or `manual`
      (default). A machine can choose its own.
    - `os` (`macos`, `linux`, `windows`), `only` and `unless` (tag lists) on
      a package, an item, a file, a step or a repo: it applies on a machine
      with one of the OSes (every OS when empty), any `only` tag (or none
      listed) and no `unless` tag (`dotfiles.os`, `dotfiles.tags`).

    ## Inputs

    `inputs` at the top are asked once and kept; a step's `inputs` are asked
    each time it runs. An input name is declared once in a package. Its
    `type` is `str`, `int`, `float`, `bool` or `choice`. `{{ name }}` in a
    `target`, a check (`command`, `prints`, `path`), `run`, `args` or a repo's
    `target` names a declared input; a secret input fits none of them, has
    no `default`, and reaches a step only as its `stdin`.

    ## Steps

    A step has a `check` (exactly one of `command`, with an optional exact
    `prints`, `path` or `program`), and a `run` (an argv, no shell), a
    `script` in the source with `args`, or a `fix` for a person. `run` and
    `script` exclude each other. `kind` with `with` names a step kind a
    plugin provides.

    ## Example

    ```yaml
    version: 1
    description: Global gitignore and identity
    policy: sync
    inputs:
      git_email: {description: Email on commits}
    items:
      ignore:
        files:
          - {path: git/ignore, target: ~/.config/git/ignore}
      identity:
        steps:
          - name: user-email
            check: {command: [git, config, --global, user.email], prints: "{{ git_email }}"}
            run: [git, config, --global, user.email, "{{ git_email }}"]
    ```
    """

    version: int = Field(strict=True, description="The package file's format: 1.")
    description: str = ""
    policy: Policy = Field(Policy.MANUAL, description="The author's suggestion.")
    inputs: dict[str, PackageInput] = Field(default_factory=dict, description="Asked once, kept.")
    items: dict[str, Item] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def _known_version(cls, value: int) -> int:
        if value > PACKAGE_VERSION:
            raise ValueError(
                f"version {value} is a newer untaped's package format "
                f"(this one reads {PACKAGE_VERSION}): upgrade untaped"
            )
        if value != PACKAGE_VERSION:
            raise ValueError(f"version {value} is not a package format (use {PACKAGE_VERSION})")
        return value

    @field_validator("inputs")
    @classmethod
    def _input_names(cls, value: dict[str, PackageInput]) -> dict[str, PackageInput]:
        _named(value, "input", INPUT_NAME)
        return value

    @field_validator("items")
    @classmethod
    def _item_names(cls, value: dict[str, Item]) -> dict[str, Item]:
        _named(value, "item")
        return value

    @model_validator(mode="after")
    def _across_items(self) -> Self:
        self._targets_unique()
        self._inputs_declared_once()
        for name, item in self.items.items():
            for file in item.files:
                self._placeholders(f"item {name!r} file {file.path!r}", "target", file.target, {})
            for repo in item.repos:
                self._placeholders(f"item {name!r} repo {repo.url!r}", "target", repo.target, {})
            for step in item.steps:
                where = f"item {name!r} step {step.name!r}"
                for field, text in step.texts():
                    self._placeholders(where, field, text, step.inputs)
                if step.stdin is not None and step.stdin not in {**self.inputs, **step.inputs}:
                    raise ValueError(f"{where}: stdin names no declared input {step.stdin!r}")
        return self

    def _targets_unique(self) -> None:
        seen: dict[str, str] = {}
        for name, item in self.items.items():
            for file in item.files:
                if file.target in seen:
                    raise ValueError(
                        f"target {file.target!r} is placed twice (items {seen[file.target]!r} "
                        f"and {name!r})"
                    )
                seen[file.target] = name

    def _inputs_declared_once(self) -> None:
        declared = set(self.inputs)
        for item in self.items.values():
            for step in item.steps:
                twice = sorted(declared & set(step.inputs))
                if twice:
                    raise ValueError(f"input {twice[0]!r} is declared twice")
                declared |= set(step.inputs)

    def _placeholders(
        self, where: str, field: str, text: str, own: Mapping[str, PackageInput]
    ) -> None:
        try:
            names = placeholders(text, field=field)
        except InputError as exc:
            raise ValueError(f"{where}: {exc}") from None
        specs = {**self.inputs, **own}
        for name in names:
            spec = specs.get(name)
            if spec is None:
                raise ValueError(f"{where}: {{{{ {name} }}}} in {field} names no declared input")
            if spec.secret:
                raise ValueError(
                    f"{where}: secret input {name!r} cannot be used in {field}; "
                    "a secret reaches a step only as its stdin"
                )


def parse_package(text: str, *, where: str) -> PackageFile:
    """Parse package ``text``; ``where`` names it in errors (``dotfiles/git``)."""
    try:
        raw: Any = yaml.safe_load(text) if text.strip() else None
    except yaml.YAMLError as exc:
        raise PackageError(f"package {where} is invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise PackageError(f"package {where} must hold a mapping with version and items")
    try:
        return PackageFile.model_validate(raw)
    except ValidationError as exc:
        raise PackageError(f"package {where}: {first_validation_error(exc)}") from exc
