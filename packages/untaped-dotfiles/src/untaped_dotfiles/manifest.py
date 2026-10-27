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
from untaped_dotfiles.errors import ManifestError
from untaped_dotfiles.models import MergeFormat, Mode, OsName
from untaped_dotfiles.policy import Policy

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


class ManifestItem(BaseModel):
    """A named unit the machine enables or ignores as a whole."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = ""
    policy: Policy = Policy.MANUAL
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
    """A repo's `dotfiles.yml`: its items, their files, and the rules they follow.

    `version: 1` is the only manifest version; a newer one is refused.

    ## Rules

    - Item names: `[a-z0-9][a-z0-9._-]*`, unique by construction.
    - `source` is relative to the repo root and stays inside it: no `..`, no
      absolute path.
    - `target` starts with `~/` or `/`, no `..`. The home directory itself, or
      a parent of it, is refused. No variable expansion, no templating.
    - `mode` defaults to `link`. `merge` needs a JSON or YAML target, chosen by
      the extension (`.json`, `.yml`, `.yaml`) or an explicit `format:`.
      `merge` does not apply to a directory source.
    - `policy` on an item is a suggestion: `sync`, `once` or `manual`.
    - `os` on an item or a file: `macos`, `linux`, `windows`. `only` and
      `unless` on an item or a file: tag lists. An entry applies when the
      machine has any `only` tag (or `only` is absent) and none of the `unless`
      tags. The machine's tags and OS are the `dotfiles.tags` and `dotfiles.os`
      settings.
    - `name` on a file defaults to its `source` and is what `enable --skip`
      takes. The same target twice in one item is an error; the same source
      twice is fine, and skipping it by source skips both placements.

    ## Modes

    - `link` places a symlink into the working tree of the clone or checkout.
      It follows the repo live, so the policy governs when a managed clone is
      pulled. Under `once` the file is placed as a copy.
    - `copy` places the file's content and records the hash of what was
      written. It follows the repo only when the tool copies again.
    - `merge` deep-merges the source document's keys into the target document.
      Mappings recurse; scalars and lists replace; keys the source does not
      mention stay. It follows the repo per managed key, like `copy`.

    `copy` and `merge` files in a managed clone are read from the fetched ref
    (`origin/<ref>`), so they follow their policy even while a `manual` link
    file holds the working tree back.

    ## Directory sources

    A directory source is placed per child: the target is a real directory and
    each file under the source gets its own link or copy at the same relative
    path. Files other programs write into the target directory are never
    touched. Each placed file has its own status row; a file that disappears
    from the source becomes `orphan`.

    ## Examples

    ```yaml
    version: 1
    items:
      fish:
        description: fish shell config
        policy: manual            # suggestion; absent means manual
        os: [macos, linux]        # absent means every OS
        files:
          - source: fish/config.fish
            target: ~/.config/fish/config.fish
            mode: link
          - source: fish/conf.d   # a directory: each file under it is placed
            target: ~/.config/fish/conf.d
      claude:
        policy: sync
        files:
          - name: settings        # names a file so a machine can skip it
            source: claude/settings.json
            target: ~/.claude/settings.json
            mode: merge
            unless: [claude-plugin-dev]
          - source: claude/skills
            target: ~/.agents/skills/mine
          - source: claude/skills # the same source may go to several targets
            target: ~/.claude/skills/mine
    ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = MANIFEST_VERSION
    items: dict[str, ManifestItem] = Field(default_factory=dict)

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
    def _item_names(cls, value: dict[str, ManifestItem]) -> dict[str, ManifestItem]:
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
