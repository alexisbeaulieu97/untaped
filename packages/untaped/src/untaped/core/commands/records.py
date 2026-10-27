"""The records the root management commands write, each declaring its ``untaped.*`` kind.

They live in one module that building the root app imports, so every kind
core declares is registered before any command runs (``plugin schema``
finds each one). A command builds these models where it makes a row, or
validates its rows at emit when they are dicts other code builds or reads
(doctor's, which ``doctor fix`` and the setup screen read; ``setup
plan``'s; ``config migrate``'s; ``skills list``'s). A table that writes a
row's command for people to type (doctor's, ``doctor fix``'s, ``setup
plan``'s) shows a dict view instead. Field order and which fields an output
omits match the dict rows these replaced.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk.diagnostic import _is_none, _Omitted
from untaped.sdk.records import (
    AbsolutePath,
    CheckRecord,
    CheckStatus,
    OutcomeRecord,
    Record,
    TargetRecord,
    _RowError,
)


class AuthOutcome(OutcomeRecord, kind="untaped.auth_outcome"):
    """What the deprecated ``auth set``, ``unset`` or ``migrate`` did to a section's token.

    Never the token. ``store`` is where it is kept (``None``: nowhere); a
    failed ``migrate`` row says why in ``detail`` and ``error``.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("section", "profile", "store", "action", "detail")

    section: str
    profile: str
    store: str | None
    action: str
    detail: _Omitted = None
    error: _RowError = None


class TokenSource(Record, kind="untaped.token_source"):
    """Where one credential comes from (``auth status``), named without reading it.

    For a declared credential, one row for this run: its key, the step that
    answers (``variable``, ``stored``, ``setting``, ``env``) and the stored
    row's id, or why a folder's settings refuse it. For a plugin still on
    11.0's ``token`` settings, one row per profile, as in 11.0.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("profile", "section", "name", "key", "source")

    profile: str
    section: str
    source: str = Field(description="What answers: a variable, `stored <id>`, a setting, `none`.")
    name: str = Field(default="token", description="The credential's name in its plugin.")
    key: _Omitted = Field(default=None, description="What the credential is for, described.")
    step: _Omitted = Field(default=None, description="Which step answers: `variable`, `stored`…")
    id: _Omitted = Field(default=None, description="The stored row's short id, when one answers.")
    refusal: _Omitted = Field(
        default=None, description="Why a folder's settings refuse this credential."
    )


class CredentialOutcome(OutcomeRecord, kind="untaped.credential_outcome"):
    """What ``auth login`` or ``auth forget`` did to one stored credential (never the secret)."""

    table_columns: ClassVar[tuple[str, ...]] = ("plugin", "name", "key", "id", "store", "action")

    plugin: str
    name: str
    key: str = Field(description="What the credential is for, described.")
    id: str = Field(description="The stored row's short id.")
    store: str | None = Field(description="Where the secret is kept, or `None`: nowhere.")
    action: str
    detail: _Omitted = None


class StoredCredential(Record, kind="untaped.stored_credential"):
    """One stored credential (``auth list``): what it is for and how it is read, not the secret."""

    table_columns: ClassVar[tuple[str, ...]] = ("id", "plugin", "name", "key", "where")

    id: str = Field(description="The row's short id, as `auth forget --id` takes it.")
    plugin: str
    name: str
    key: str = Field(description="What the credential is for, described.")
    backend: str = Field(description="`security`, `secret-tool`, `pass` or `command`.")
    where: str = Field(description="The store entry, or the command's program alone.")


class ConfigMigrationOutcome(OutcomeRecord, kind="untaped.config_migration_outcome"):
    """One key ``config migrate`` renamed, dropped or deleted in one profile."""

    table_columns: ClassVar[tuple[str, ...]] = ("profile", "from", "to", "action", "detail")

    model_config = ConfigDict(serialize_by_alias=True)

    profile: str
    from_: str = Field(alias="from")
    to: str = Field(description="Empty for a deleted key.")
    action: str
    detail: str = Field(description="For a deleted key, the value it held and why it went.")


class DoctorCheck(CheckRecord, kind="untaped.doctor_check"):
    """One ``doctor`` check; ``fix`` is the command that fixes it, as arguments."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "plugin",
        "check",
        "status",
        "title",
        "detail",
        "fix",
    )

    check: str
    plugin: str
    status: CheckStatus
    title: str
    detail: str
    fix: list[str] | None
    automatic: bool = Field(description="Whether `doctor fix` runs `fix` on its own.")


class FixOutcome(OutcomeRecord, kind="untaped.fix_outcome"):
    """What one fix ``doctor fix`` ran (or would run) did to the checks it covers."""

    fix: list[str]
    checks: list[str]
    action: str
    detail: str
    error: _RowError = None


class RegisteredHolder(BaseModel):
    """Who a registration is for, as ``setup register`` names it: ``install`` and its id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str


class RegisteredTarget(BaseModel):
    """What a registration keeps: one ``layout`` of a store, or one ``item`` in it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plugin: str
    store: str
    layout: Annotated[int | None, Field(exclude_if=_is_none)] = None
    item: Annotated[str | None, Field(exclude_if=_is_none)] = None


class RegisterOutcome(OutcomeRecord, kind="untaped.register_outcome"):
    """What ``setup register`` did: ``registered``, ``unchanged``, ``removed`` or ``gone``.

    ``environment`` is the directory holding untaped's distribution,
    ``targets`` the store layouts the reference names. Its own models, not
    the references' (core's to change), so the record keeps its shape.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("action", "version", "environment", "targets")

    holder: Annotated[RegisteredHolder | None, Field(exclude_if=_is_none)] = None
    environment: str
    version: str | None
    targets: list[RegisteredTarget]


PruneWhat = Literal["reference", "folder"]
"""What a ``setup prune`` row is about: an install's reference, or a plugin's folder."""


class PruneOutcome(OutcomeRecord, TargetRecord, kind="untaped.prune_outcome"):
    """What ``setup prune`` did, or would do, to one dead reference or leftover plugin folder.

    ``target_path`` is the reference file or the ``plugins/<name>/`` folder.
    ``action`` is ``planned`` (``--dry-run``), ``removed``, ``kept`` (it
    stays, ``detail`` says why), ``skipped`` (it changed since the preview)
    or ``failed``, with ``error``.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("what", "target_path", "action", "detail")

    what: PruneWhat
    detail: str
    bytes: int = Field(description="Its size on disk when it was listed.")


ProjectState = Literal["present", "unreadable", "missing", "unknown"]
"""Where a listed project stands; ``unknown`` is a record that can't be read."""


class RecordedProject(Record, kind="untaped.recorded_project"):
    """One project the home records (``project list``).

    ``path`` is its folder; ``holds`` the items it holds, per ``plugin.store``.
    An ``unknown`` row is a record untaped can't read: no ``id``, its file as
    ``path`` and why in ``detail``.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("name", "path", "state", "current")

    name: str
    path: str
    state: ProjectState
    current: bool = Field(description="Whether the working directory is in it.")
    id: _Omitted = None
    holds: dict[str, list[str]] = Field(default_factory=dict)
    detail: _Omitted = None


class ProjectOutcome(OutcomeRecord, kind="untaped.project_outcome"):
    """What ``project init`` did to one folder.

    ``action`` is ``created``, ``unchanged``, ``recorded`` (the home now
    lists it), ``repaired`` (its record follows a move) or ``new id`` (a copy
    given its own).
    """

    table_columns: ClassVar[tuple[str, ...]] = ("name", "path", "action", "detail")

    name: str
    path: str
    id: str
    detail: str


SetupState = Literal["done", "todo", "failed", "skipped"]
"""Where a ``setup plan`` step stands."""

SetupActor = Literal["agent", "user"]
"""Who takes a ``setup plan`` step."""


class SetupStep(Record, kind="untaped.setup_step"):
    """One step of ``setup plan``; ``run`` is the command that does it, as arguments."""

    table_columns: ClassVar[tuple[str, ...]] = ("step", "state", "by", "detail", "run")

    step: str
    plugin: str
    state: SetupState
    detail: str
    run: list[str] = Field(
        description=(
            "The complete argv to pass after `untaped`; a `<NAME>` token is a value to ask for."
        )
    )
    by: SetupActor = Field(
        description="Whose step it is: the user's when a secret would pass through the agent."
    )


class PluginCheckRow(CheckRecord, kind="untaped.plugin_check"):
    """One finding of a plugin check (:func:`untaped.sdk.testing.check_plugin`)."""

    table_columns: ClassVar[tuple[str, ...]] = ("plugin", "check", "title", "status", "detail")

    plugin: str
    check: str = Field(
        description=(
            "`conventions`, `registration`, `conformance`, `live`, `unmarked-service` or "
            "`contracts`."
        )
    )
    title: str = Field(
        description=(
            "What it is about: a convention rule, `owner.contract` or `owner.contract.method`."
        )
    )
    detail: str = ""


class ScaffoldOutcome(OutcomeRecord, TargetRecord, kind="untaped.scaffold_outcome"):
    """One file ``plugin new`` wrote (``created``) or would write (``planned``)."""

    table_columns: ClassVar[tuple[str, ...]] = ("action", "target_path")


class Skill(Record, kind="untaped.skill"):
    """A stub skill the installed plugins ship (``skills list``)."""

    name: str
    description: str


class InstalledSkill(TargetRecord, kind="untaped.installed_skill"):
    """An untaped-installed skill directory and whether it matches this version."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "name",
        "target",
        "scope",
        "state",
        "target_path",
    )

    name: str
    target: str
    scope: str
    state: str


class SkillOutcome(OutcomeRecord, TargetRecord, kind="untaped.skill_outcome"):
    """What ``skills update`` or ``skills remove`` did to one installed skill."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "target", "scope", "target_path", "action")

    name: str
    target: str
    scope: str
    target_path: AbsolutePath
    action: str
    error: _RowError = None
