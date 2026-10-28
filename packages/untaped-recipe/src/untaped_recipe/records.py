"""The records recipe's commands print; each class names its ``recipe.*`` kind.

A kind has one schema, so where a ``list`` row and a ``get`` detail share a
kind (``recipe.recipe``, ``recipe.pack``, ``recipe.hook``,
``recipe.backup``) one model carries both: the fields only ``get`` reads
are ``null`` on a ``list`` row. ``recipe.pack``'s ``recipes`` and
``hooks`` are counts in ``packs list`` and summaries in ``packs get``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from untaped.sdk import (
    AbsolutePath,
    CheckRecord,
    Column,
    Failure,
    Following,
    Key,
    ObjectName,
    OutcomeRecord,
    Record,
    Status,
    TargetRecord,
    UtcTimestamp,
)


class ApplyOutcome(OutcomeRecord, TargetRecord):
    """One target's ``recipe apply`` result.

    ``action`` is ``planned`` (preview), ``applied``, ``unchanged``,
    ``skipped`` (a validate hook marked the target not applicable) or
    ``failed``. A declined confirmation prints no rows. A failed row says
    why in ``detail`` (for humans) and ``error`` (category, system, message).
    """

    target_path: Annotated[AbsolutePath, Column]
    action: Annotated[str, Column, Status(success=("applied",))]
    files_changed: Annotated[int, Column]
    warnings: Annotated[list[str], Column]
    detail: Annotated[str | None, Column]
    inputs: dict[str, object]
    recipe: str


class _PackOutcome(OutcomeRecord):
    """An installed pack's ``add``/``sync``/``remove`` result.

    A pack that could not be fetched, installed or removed is ``failed``,
    with ``detail`` and ``error``. ``commit`` is the resolved commit of a git
    source (``rev`` is the one asked for).
    """

    name: Annotated[str, Column]
    action: Annotated[str, Column, Status(success=("removed",))]
    source: Annotated[str | None, Column] = None
    rev: Annotated[str | None, Column] = None
    commit: Annotated[str | None, Column, ObjectName] = None
    detail: Annotated[str | None, Column] = None


class AddOutcome(_PackOutcome):
    """What ``packs add`` did: ``created`` or ``updated``."""


class SyncOutcome(_PackOutcome):
    """What ``packs sync`` did: ``updated``, ``unchanged``, ``planned`` (``--dry-run``)."""


class RemoveOutcome(_PackOutcome):
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


class Pack(Record):
    """An installed pack (``packs list``, ``packs get``).

    ``recipes`` and ``hooks`` count them in ``packs list`` and summarize each
    in ``packs get``. ``manifest_name`` is the manifest's own name when the
    pack was installed under another (``packs add --name``). ``version`` is
    the installed version in ``packs list`` and the library copy's manifest
    version in ``packs get``.
    """

    name: Annotated[str, Column, Key]
    project: str
    version: Annotated[str, Column]
    path: str
    source: Annotated[str, Column]
    rev: Annotated[str, Column]
    commit: Annotated[str, ObjectName]
    recipes: Annotated[int | list[PackRecipe], Column]
    hooks: Annotated[int | list[PackHook], Column]
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


class Recipe(Record):
    """An installed recipe (``recipe list``, ``recipe get``).

    ``description``, ``inputs``, ``steps`` and ``hooks`` are read from the
    recipe file by ``recipe get`` only; ``recipe list`` leaves them null.
    """

    pack: Annotated[str, Column]
    name: Annotated[str, Column]
    ref: str
    description: str | None = None
    inputs: Annotated[list[RecipeInput] | None, Following] = None
    steps: Annotated[list[RecipeStep] | None, Following] = None
    hooks: list[str] | None = None
    path: str


class Hook(Record):
    """An installed or built-in hook (``hooks list``, ``hooks get``).

    A built-in's ``pack`` is ``(builtin)``. ``exports`` (the callables the
    module defines) is read by ``hooks get`` only; ``hooks list`` leaves it
    null.
    """

    pack: Annotated[str, Column]
    name: Annotated[str, Column]
    ref: str
    module: Annotated[str, Column]
    exports: list[str] | None = None
    path: str


HookRunStatus = Literal["ok", "pass", "fail", "skip"]
"""A ``hooks run`` row's ``status``: ``ok`` for a transform, else the verdict."""


class HookRun(Record):
    """What ``hooks run`` got from one hook.

    A ``transform`` row has the target-relative ``file``, the transformed
    ``content`` and, with ``--diff``, its ``diff``; a ``validate`` row has
    the verdict's ``status`` and ``message``; a ``fail`` verdict also
    carries a structured ``error``, which makes the run exit 1. The other
    kind's fields are null.
    """

    hook: Annotated[str, Column]
    kind: Annotated[Literal["transform", "validate"], Column]
    target: str
    file: Annotated[str | None, Column] = None
    status: Annotated[
        HookRunStatus, Column, Status(success=("ok", "pass"), warning=("skip",), error=("fail",))
    ]
    content: str | None = None
    diff: str | None = None
    message: Annotated[str | None, Column] = None
    error: Failure | None = None


CheckedType = Literal["pack", "recipe", "hook"]
"""What a ``recipe validate`` row checked."""


class Check(CheckRecord):
    """One ``recipe validate`` row.

    ``name`` is the pack name, the ``PACK/RECIPE`` ref, or the built-in hook
    name, and ``type`` says which, so a listing mixing packs and recipes keeps
    one identifying column. ``status`` is ``pass`` or ``fail``; ``detail``
    says why a check failed.
    """

    name: Annotated[str, Column]
    type: Annotated[CheckedType, Column]
    status: Annotated[Literal["pass", "fail"], Column]
    path: Annotated[str, Column]
    detail: Annotated[str | None, Column] = None


class Test(Record):
    """One golden case ``recipe test`` ran (or updated with ``--update``).

    ``status`` is ``pass``, ``fail``, ``error`` or ``updated``; an ``error``
    row also carries a structured ``error``.
    """

    pack: str
    recipe: Annotated[str, Column]
    case: Annotated[str, Column]
    status: Annotated[
        Literal["pass", "fail", "error", "updated"],
        Column,
        Status(success=("pass", "updated"), error=("fail", "error")),
    ]
    detail: Annotated[str, Column]
    error: Failure | None = None


class BackupFile(BaseModel):
    """One file a backup bundle holds, as ``backups get`` lists it.

    ``backup_file`` (relative to the bundle) is ``None`` when the apply created
    the file, so a restore deletes it.
    """

    model_config = ConfigDict(frozen=True)

    target: Annotated[str, Column]
    relative_path: Annotated[str, Column]
    before_hash: str | None = None
    after_hash: str | None
    backup_file: str | None
    inputs: dict[str, JsonValue] = {}


class Backup(Record):
    """A backup bundle (``backups list``, ``backups get``).

    ``created_at`` comes from the bundle id; ``recipe`` from its metadata
    (null when that cannot be read). ``inputs`` and ``files`` (one entry per
    backed-up file, as the bundle's metadata holds it) are read by
    ``backups get`` only; ``backups list`` leaves them null.
    """

    id: Annotated[str, Column]
    created_at: Annotated[UtcTimestamp | None, Column]
    recipe: Annotated[str | None, Column]
    path: str
    inputs: dict[str, JsonValue] | None = None
    files: Annotated[list[BackupFile] | None, Following] = None


class RestoreOutcome(OutcomeRecord):
    """The ``backups restore`` row.

    ``files`` counts the bundle's files. ``action`` is ``planned``
    (``--dry-run``), ``restored`` or ``failed`` (with ``detail`` and ``error``).
    """

    id: str
    action: Annotated[str, Status(success=("restored",))]
    files: int
    detail: str | None = None


class PruneOutcome(OutcomeRecord):
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
    "BackupFile",
    "Check",
    "CheckedType",
    "Hook",
    "HookRun",
    "Pack",
    "PackHook",
    "PackRecipe",
    "PruneOutcome",
    "Recipe",
    "RecipeInput",
    "RecipeStep",
    "RemoveOutcome",
    "RestoreOutcome",
    "SyncOutcome",
    "Test",
]
