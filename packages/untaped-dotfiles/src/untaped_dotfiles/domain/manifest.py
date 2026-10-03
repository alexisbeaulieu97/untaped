"""The manifest (``dotfiles.yml``): items, their files, and every rule they must follow.

A manifest is read from a subscribed repo, never written. Its ``version``
is refused when unknown, like ``format_version`` in the config files, so an
older tool never misreads a newer manifest.
"""

from __future__ import annotations

import re
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from untaped.sdk import first_validation_error
from untaped_dotfiles.domain.models import MergeFormat, Mode, OsName, Policy
from untaped_dotfiles.errors import ManifestError

MANIFEST_VERSION = 1
DEFAULT_MANIFEST = "dotfiles.yml"
_ITEM_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_FORMATS: dict[str, MergeFormat] = {".json": "json", ".yml": "yaml", ".yaml": "yaml"}


def _relative_inside(value: str, what: str) -> str:
    cleaned = value.strip().replace("\\", "/").strip("/")
    if not cleaned or value.startswith(("/", "~")) or ":" in value.split("/", 1)[0]:
        raise ValueError(f"{what} must be a path relative to the repo root")
    if any(part in ("", ".", "..") for part in cleaned.split("/")):
        raise ValueError(f"{what} must not contain '.' or '..' segments")
    return cleaned


class FileEntry(BaseModel):
    """One placement: ``source`` in the repo to ``target`` on the machine, by ``mode``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    target: str
    mode: Mode = "link"
    name: str | None = None
    format: MergeFormat | None = None
    os: tuple[OsName, ...] = ()
    only: tuple[str, ...] = ()
    unless: tuple[str, ...] = ()

    @field_validator("source")
    @classmethod
    def _source_inside_repo(cls, value: str) -> str:
        return _relative_inside(value, "source")

    @field_validator("target")
    @classmethod
    def _target_is_home_relative_or_absolute(cls, value: str) -> str:
        if value == "~" or value.startswith("~/") or value.startswith("/"):
            if any(part == ".." for part in value.split("/")):
                raise ValueError("target must not contain '..' segments")
            return value
        raise ValueError("target must start with '~/' or '/'")

    @property
    def key(self) -> str:
        """The name a machine skips the file by: ``name``, else ``source``."""
        return self.name or self.source

    @property
    def merge_format(self) -> MergeFormat | None:
        """``merge`` only: the explicit ``format``, else the one the target's extension names."""
        if self.mode != "merge":
            return None
        if self.format is not None:
            return self.format
        suffix = self.target.rsplit("/", 1)[-1]
        dot = suffix.rfind(".")
        return _FORMATS.get(suffix[dot:].lower()) if dot >= 0 else None

    @model_validator(mode="after")
    def _merge_has_a_format(self) -> Self:
        if self.mode == "merge" and self.merge_format is None:
            raise ValueError(
                f"merge target {self.target!r} needs a .json, .yml or .yaml extension or a format"
            )
        if self.mode != "merge" and self.format is not None:
            raise ValueError("format applies to merge files only")
        return self


class Item(BaseModel):
    """A named unit the machine enables or ignores as a whole."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = ""
    policy: Policy = "manual"
    os: tuple[OsName, ...] = ()
    only: tuple[str, ...] = ()
    unless: tuple[str, ...] = ()
    files: tuple[FileEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _targets_are_unique(self) -> Self:
        """A source may go to several targets; the same target twice is a mistake."""
        seen: set[str] = set()
        for entry in self.files:
            if entry.target in seen:
                raise ValueError(f"target {entry.target!r} is listed twice")
            seen.add(entry.target)
        return self


class Manifest(BaseModel):
    """The parsed manifest of one repo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = MANIFEST_VERSION
    items: dict[str, Item] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _known_version(cls, value: int) -> int:
        if value != MANIFEST_VERSION:
            raise ValueError(
                f"manifest version {value} is not supported (this untaped reads version "
                f"{MANIFEST_VERSION}); upgrade untaped"
            )
        return value

    @field_validator("items")
    @classmethod
    def _item_names(cls, value: dict[str, Item]) -> dict[str, Item]:
        for name in value:
            if not _ITEM_NAME.match(name):
                raise ValueError(
                    f"item name {name!r} must be lowercase letters, digits, '.', '_' or '-'"
                )
        return value


def parse_manifest(text: str, *, where: str) -> Manifest:
    """Parse manifest ``text``; ``where`` names it in errors (``<repo>:dotfiles.yml``)."""
    try:
        raw: Any = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifest {where} is invalid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ManifestError(f"manifest {where} must contain a mapping")
    try:
        return Manifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(f"manifest {where}: {first_validation_error(exc)}") from exc
