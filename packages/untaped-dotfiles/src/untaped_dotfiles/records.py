"""The records the dotfiles commands print; each kind is its class name (``dotfiles.entry_status``).

It holds models only, so importing it never imports a command.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from untaped.sdk import (
    AbsolutePath,
    Column,
    Failure,
    OutcomeRecord,
    Record,
    UtcTimestamp,
)
from untaped_dotfiles.package import Mode
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.states import ENTRY_STATE_ROLES, EntryState, FileState

EntryKind = Literal["file", "step", "repo"]


class Package(Record):
    """One package a source offers, and whether this machine turned it on."""

    source: Annotated[str, Column]
    name: Annotated[str, Column]
    on: Annotated[bool, Column]
    policy: Annotated[Policy | None, Column] = Field(
        default=None,
        description="The policy its entries follow here: this machine's choice, else the "
        "author's; null while the package is off.",
    )
    suggested: Annotated[Policy | None, Column] = Field(
        default=None, description="The author's policy; null when the file doesn't load."
    )
    chosen: bool = Field(False, description="Whether `policy` is this machine's choice.")
    items: Annotated[int, Column] = 0
    description: Annotated[str, Column] = ""
    excluded: str | None = Field(
        default=None, description="Why the package doesn't apply on this machine, or null."
    )
    error: Failure | None = Field(
        default=None, description="Why the package file doesn't load, or null."
    )


class _Detail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputDetail(_Detail):
    """One input a package declares, and the value this machine gives it."""

    name: str
    secret: bool = False
    default: JsonValue | None = None
    value: JsonValue | None = Field(
        default=None, description="The value used here; null for a secret or no value."
    )
    origin: Literal["config", "default"] | None = Field(
        default=None,
        description="Where `value` comes from: `config` (this machine's answer), `default` "
        "(the package's), or null while it has none.",
    )


class FileDetail(_Detail):
    """One file entry: where it goes and how, and its state."""

    path: str
    target: str = Field(description="As the package writes it.")
    mode: Mode
    placed_as: Mode | None = Field(
        default=None,
        description="How it is placed here: a `link` only under `sync`, else a copy; "
        "null while the item is off.",
    )
    excluded: str | None = None
    state: FileState | None = Field(
        default=None, description="Its state here; null while the item is off."
    )


class CheckDetail(_Detail):
    """A step's check."""

    command: tuple[str, ...] | None = None
    prints: str | None = None
    path: str | None = None
    program: str | None = None


class StepDetail(_Detail):
    """One step: what it checks and runs, as the package writes it (nothing is run to show it)."""

    name: str
    description: str = ""
    policy: Policy = Field(
        description="The policy the step follows: this machine's choice, else `manual`."
    )
    chosen: bool = False
    check: CheckDetail | None = None
    run: tuple[str, ...] | None = None
    script: str | None = None
    args: tuple[str, ...] = ()
    stdin: str | None = None
    fix: str | None = None
    kind: str | None = None
    excluded: str | None = None


class RepoDetail(_Detail):
    """One working copy an item clones."""

    url: str
    target: str
    branch: str | None = None
    excluded: str | None = None


class ItemDetail(_Detail):
    """One item of a package, with its entries."""

    name: str
    description: str = ""
    enabled: bool
    policy: Policy | None = None
    excluded: str | None = None
    files: tuple[FileDetail, ...] = ()
    steps: tuple[StepDetail, ...] = ()
    repos: tuple[RepoDetail, ...] = ()


class PackageDetail(Record):
    """A package in full: its inputs, items and entries, and what this machine chose."""

    source: Annotated[str, Column]
    name: Annotated[str, Column]
    on: Annotated[bool, Column]
    policy: Annotated[Policy | None, Column] = None
    suggested: Annotated[Policy | None, Column] = None
    chosen: bool = False
    description: Annotated[str, Column] = ""
    excluded: str | None = None
    inputs: tuple[InputDetail, ...] = ()
    items: tuple[ItemDetail, ...] = ()
    error: Failure | None = Field(
        default=None, description="Why the package file doesn't load, or null."
    )


class EntryStatus(Record):
    """The state of one entry, a file or a step, of a package that is on or was applied before."""

    source: str
    package: Annotated[str, Column]
    item: Annotated[str, Column]
    kind: Annotated[EntryKind, Column] = "file"
    entry: Annotated[str, Column] = Field(
        description="A file's target as the user reads it (`~/...`), or a step's name."
    )
    mode: Annotated[Mode | None, Column] = Field(
        default=None,
        description="How the file is placed here (a `link` only under `sync`); null for a step.",
    )
    policy: Annotated[Policy | None, Column] = Field(
        description="The policy the entry follows: for a file, this machine's choice else the "
        "author's; for a step, this machine's choice else `manual`; null for a working copy, "
        "which is cloned once whatever the policy."
    )
    chosen: bool = False
    state: Annotated[EntryState, Column, ENTRY_STATE_ROLES] = Field(
        description="The entry's state; the policy guide explains each one."
    )
    detail: Annotated[str, Column] = ""
    target_path: AbsolutePath | None = Field(
        default=None, description="A file's absolute target; null for a step."
    )
    error: Failure | None = None


class StatusSummary(Record):
    """Counts over the status rows, also written to ``status.json``."""

    checked_at: UtcTimestamp
    total: int
    apply: int = Field(
        0,
        description="Rows the next `apply` (no selector) changes on its own, by their policy.",
    )
    attention: int = Field(
        description="Rows only a person can settle, `error` rows included. With `apply`, they "
        "make `status --check` exit 3 (1 when one is an `error` row)."
    )
    pending: int = 0
    foreign: int = 0
    new: int = 0
    applied: int = 0
    behind: int = 0
    modified: int = 0
    conflict: int = 0
    missing: int = 0
    orphan: int = 0
    off: int = 0
    drifted: int = 0
    unmet: int = 0
    error: int = 0


ApplyAction = Literal[
    "planned", "created", "updated", "unchanged", "skipped", "deleted", "conflict", "failed"
]


class ApplyOutcome(OutcomeRecord):
    """What ``apply``, ``remove`` or ``unsubscribe`` did, or would do, for one entry."""

    source: str
    package: Annotated[str, Column]
    item: Annotated[str, Column]
    kind: Annotated[EntryKind, Column] = "file"
    entry: Annotated[str, Column]
    mode: Annotated[Mode | None, Column] = None
    action: Annotated[ApplyAction, Column]
    state: Annotated[EntryState, ENTRY_STATE_ROLES] | None = Field(
        default=None,
        description="The entry's state before the action, so a `skipped` row that was "
        "`behind` differs from one that was `applied`; the policy guide explains each state.",
    )
    detail: Annotated[str, Column] = ""
    diff: str | None = Field(
        default=None,
        description="A unified diff of the target's content, before and after, when the "
        "action changes it (a new file's whole content included); null otherwise.",
    )
    target_path: AbsolutePath | None = Field(
        default=None, description="A file's absolute target; null for a step."
    )
