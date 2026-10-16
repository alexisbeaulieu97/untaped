"""The records recipe's commands write, each declaring its ``recipe.*`` kind.

Every CLI module imports its models from here, so building the app
registers every kind before any command runs (``plugin schema`` finds each
one). This module holds models only: it sits on the app's load path.

A kind has one schema, so where a ``list`` row and a ``get`` detail share a
kind (``recipe.recipe``, ``recipe.pack``, ``recipe.hook``,
``recipe.backup``) one model carries both: the fields only ``get`` reads
are ``null`` on a ``list`` row. ``recipe.pack``'s ``recipes`` and
``hooks`` are counts in ``packs list`` and summaries in ``packs get``.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from untaped.sdk import (
    AbsolutePath,
    CheckRecord,
    Failure,
    OutcomeRecord,
    Record,
    TargetRecord,
    UtcTimestamp,
)


class ApplyOutcome(OutcomeRecord, TargetRecord, kind="recipe.apply_outcome"):
    """One target's ``recipe apply`` result.

    ``action`` is ``planned`` (preview), ``applied``, ``unchanged``,
    ``skipped`` (a validate hook marked the target not applicable),
    ``cancelled`` (confirmation declined) or ``failed``. A failed row says
    why in ``detail`` (for humans) and ``error`` (category, system, message).
    """

    table_columns: ClassVar[tuple[str, ...]] = (
        "target_path",
        "action",
        "files_changed",
        "warnings",
        "detail",
    )

    target_path: AbsolutePath
    files_changed: int
    warnings: list[str]
    detail: str | None
    inputs: dict[str, object]
    recipe: str


class _PackOutcome(OutcomeRecord):
    """An installed pack's ``add``/``sync``/``remove`` result.

    A pack that could not be fetched, installed or removed is ``failed``,
    with ``detail`` and ``error``. ``commit`` is the resolved commit of a git
    source (``rev`` is the one asked for).
    """

    table_columns: ClassVar[tuple[str, ...]] = (
        "name",
        "action",
        "source",
        "rev",
        "commit",
        "detail",
    )

    name: str
    source: str | None = None
    rev: str | None = None
    commit: str | None = None
    detail: str | None = None


class AddOutcome(_PackOutcome, kind="recipe.add_outcome"):
    """What ``packs add`` did: ``created`` or ``updated``."""


class SyncOutcome(_PackOutcome, kind="recipe.sync_outcome"):
    """What ``packs sync`` did: ``updated``, ``unchanged``, ``planned`` (``--dry-run``)."""


class RemoveOutcome(_PackOutcome, kind="recipe.remove_outcome"):
    """What ``packs remove`` did: ``removed``, ``partial`` or ``planned`` (``--dry-run``)."""


class PackRecipe(BaseModel):
    """One recipe of a pack in ``packs get``: its name and first description line."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str


class PackHook(BaseModel):
    """One hook of a pack in ``packs get``: its name and the callables it exports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    exports: list[str]


class Pack(Record, kind="recipe.pack"):
    """An installed pack (``packs list``, ``packs get``).

    ``recipes`` and ``hooks`` count them in ``packs list`` and summarize each
    in ``packs get``. ``manifest_name`` is the manifest's own name when the
    pack was installed under another (``packs add --name``). ``version`` is
    the installed version in ``packs list`` and the library copy's manifest
    version in ``packs get``.
    """

    table_columns: ClassVar[tuple[str, ...]] = (
        "name",
        "version",
        "source",
        "rev",
        "recipes",
        "hooks",
    )

    name: str
    project: str
    version: str
    path: str
    source: str
    rev: str
    commit: str
    recipes: int | list[PackRecipe]
    hooks: int | list[PackHook]
    manifest_name: str | None = None


class RecipeInput(BaseModel):
    """One declared input of a recipe in ``recipe get``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str
    required: bool
    default: object
    description: str
    sensitive: bool


class RecipeStep(BaseModel):
    """One step of a recipe in ``recipe get``: the files it touches and its hook."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    files: list[str]
    globs: list[str]
    exclude: list[str]
    hook: str


class InstalledRecipe(Record, kind="recipe.recipe"):
    """An installed recipe (``recipe list``, ``recipe get``).

    ``description``, ``inputs``, ``steps`` and ``hooks`` are read from the
    recipe file by ``recipe get`` only; ``recipe list`` leaves them null.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("pack", "name")

    pack: str
    name: str
    ref: str
    description: str | None = None
    inputs: list[RecipeInput] | None = None
    steps: list[RecipeStep] | None = None
    hooks: list[str] | None = None
    path: str


class Hook(Record, kind="recipe.hook"):
    """An installed or built-in hook (``hooks list``, ``hooks get``).

    A built-in's ``pack`` is ``(builtin)``. ``exports`` (the callables the
    module defines) is read by ``hooks get`` only; ``hooks list`` leaves it
    null.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("pack", "name", "module")

    pack: str
    name: str
    ref: str
    module: str
    exports: list[str] | None = None
    path: str


HookRunStatus = Literal["ok", "pass", "fail", "skip"]
"""A ``hooks run`` row's ``status``: ``ok`` for a transform, else the verdict."""


class HookRun(Record, kind="recipe.hook_run"):
    """What ``hooks run`` got from one hook.

    A ``transform`` row has the target-relative ``file``, the transformed
    ``content`` and, with ``--diff``, its ``diff``; a ``validate`` row has
    the verdict's ``status`` and ``message``. The other kind's fields are
    null.
    """

    hook: str
    kind: Literal["transform", "validate"]
    target: str
    file: str | None = None
    status: HookRunStatus
    content: str | None = None
    diff: str | None = None
    message: str | None = None


CheckedType = Literal["pack", "recipe", "hook"]
"""What a ``recipe validate`` row checked."""


class LibraryCheck(CheckRecord, kind="recipe.check"):
    """One ``recipe validate`` row.

    ``name`` is the pack name, the ``PACK/RECIPE`` ref, or the built-in hook
    name, and ``type`` says which, so a listing mixing packs and recipes keeps
    one identifying column. ``status`` is ``pass`` or ``fail``; ``detail``
    says why a check failed.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("name", "type", "status", "path", "detail")

    name: str
    type: CheckedType
    status: Literal["pass", "fail"]
    path: str
    detail: str | None = None


class GoldenCase(Record, kind="recipe.test"):
    """One golden case ``recipe test`` ran (or updated with ``--update``).

    ``status`` is ``pass``, ``fail``, ``error`` or ``updated``; an ``error``
    row also carries a structured ``error``.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("recipe", "case", "status", "detail")

    pack: str
    recipe: str
    case: str
    status: Literal["pass", "fail", "error", "updated"]
    detail: str
    error: Failure | None = None


class Backup(Record, kind="recipe.backup"):
    """A backup bundle (``backups list``, ``backups get``).

    ``created_at`` comes from the bundle id; ``recipe`` from its metadata
    (null when that cannot be read). ``inputs`` and ``files`` (one entry per
    backed-up file, as the bundle's metadata holds it) are read by
    ``backups get`` only; ``backups list`` leaves them null.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("id", "created_at", "recipe")

    id: str
    created_at: UtcTimestamp | None
    recipe: str | None
    path: str
    inputs: dict[str, JsonValue] | None = None
    files: list[dict[str, JsonValue]] | None = None


class RestoreOutcome(OutcomeRecord, kind="recipe.restore_outcome"):
    """The ``backups restore`` row.

    ``files`` counts the bundle's files. ``action`` is ``planned``
    (``--dry-run``), ``restored`` or ``failed`` (with ``detail`` and ``error``).
    """

    id: str
    files: int
    detail: str | None = None


class PruneOutcome(OutcomeRecord, kind="recipe.prune_outcome"):
    """One ``backups prune`` row.

    ``action`` is ``planned`` (``--dry-run``), ``deleted`` or ``failed`` (with
    ``detail`` and ``error``).
    """

    id: str
    size_bytes: int
    detail: str | None = None


__all__ = [
    "AddOutcome",
    "ApplyOutcome",
    "Backup",
    "CheckedType",
    "GoldenCase",
    "Hook",
    "HookRun",
    "InstalledRecipe",
    "LibraryCheck",
    "Pack",
    "PackHook",
    "PackRecipe",
    "PruneOutcome",
    "RecipeInput",
    "RecipeStep",
    "RemoveOutcome",
    "RestoreOutcome",
    "SyncOutcome",
]
