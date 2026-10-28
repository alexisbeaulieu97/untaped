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

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk.diagnostic import _is_none, _Omitted
from untaped.sdk.fields import Column
from untaped.sdk.records import (
    AbsolutePath,
    CheckRecord,
    CheckStatus,
    OutcomeRecord,
    Record,
    TargetRecord,
    _RowError,
)


class TokenSource(Record, kind="untaped.token_source"):
    """Where one credential comes from (``auth status``), named without reading it.

    One row per credential for this run: its key, the step that answers
    (``variable``, ``stored``, ``env``) and the stored row's id, or why a
    folder's settings refuse it.
    """

    profile: Annotated[str, Column]
    section: Annotated[str, Column]
    source: Annotated[str, Column] = Field(
        description="What answers: a variable, `stored <id>`, a setting, `none`."
    )
    name: Annotated[str, Column] = Field(
        default="token", description="The credential's name in its plugin."
    )
    key: Annotated[_Omitted, Column] = Field(
        default=None, description="What the credential is for, described."
    )
    step: _Omitted = Field(default=None, description="Which step answers: `variable`, `stored`…")
    id: _Omitted = Field(default=None, description="The stored row's short id, when one answers.")
    refusal: _Omitted = Field(
        default=None, description="Why a folder's settings refuse this credential."
    )


class CredentialOutcome(OutcomeRecord, kind="untaped.credential_outcome"):
    """What ``auth login`` or ``auth forget`` did to one stored credential (never the secret)."""

    plugin: Annotated[str, Column]
    name: Annotated[str, Column]
    key: Annotated[str, Column] = Field(description="What the credential is for, described.")
    id: Annotated[str, Column] = Field(description="The stored row's short id.")
    store: Annotated[str | None, Column] = Field(
        description="Where the secret is kept, or `None`: nowhere."
    )
    action: Annotated[str, Column]
    detail: _Omitted = None


class StoredCredential(Record, kind="untaped.stored_credential"):
    """One stored credential (``auth list``): what it is for and how it is read, not the secret."""

    id: Annotated[str, Column] = Field(
        description="The row's short id, as `auth forget --id` takes it."
    )
    plugin: Annotated[str, Column]
    name: Annotated[str, Column]
    key: Annotated[str, Column] = Field(description="What the credential is for, described.")
    backend: str = Field(description="`security`, `secret-tool`, `pass` or `command`.")
    where: Annotated[str, Column] = Field(
        description="The store entry, or the command's program alone."
    )


class ConfigMigrationOutcome(OutcomeRecord, kind="untaped.config_migration_outcome"):
    """One key ``config migrate`` renamed, dropped or deleted in one profile."""

    model_config = ConfigDict(serialize_by_alias=True)

    profile: Annotated[str, Column]
    from_: Annotated[str, Column] = Field(alias="from")
    to: Annotated[str, Column] = Field(description="Empty for a deleted key.")
    action: Annotated[str, Column]
    detail: Annotated[str, Column] = Field(
        description="For a deleted key, the value it held and why it went."
    )


class DoctorCheck(CheckRecord, kind="untaped.doctor_check"):
    """One ``doctor`` check; ``fix`` is the command that fixes it, as arguments."""

    check: Annotated[str, Column]
    plugin: Annotated[str, Column]
    status: Annotated[CheckStatus, Column]
    title: Annotated[str, Column]
    detail: Annotated[str, Column]
    fix: Annotated[list[str] | None, Column]
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

    holder: Annotated[RegisteredHolder | None, Field(exclude_if=_is_none)] = None
    environment: Annotated[str, Column]
    version: Annotated[str | None, Column]
    targets: Annotated[list[RegisteredTarget], Column]


PruneWhat = Literal["reference", "folder"]
"""What a ``setup prune`` row is about: an install's reference, or a plugin's folder."""


class PruneOutcome(OutcomeRecord, TargetRecord, kind="untaped.prune_outcome"):
    """What ``setup prune`` did, or would do, to one dead reference or leftover plugin folder.

    ``target_path`` is the reference file or the ``plugins/<name>/`` folder.
    ``action`` is ``planned`` (``--dry-run``), ``removed``, ``kept`` (it
    stays, ``detail`` says why), ``skipped`` (it changed since the preview)
    or ``failed``, with ``error``.
    """

    what: Annotated[PruneWhat, Column]
    detail: Annotated[str, Column]
    bytes: int = Field(description="Its size on disk when it was listed.")
    target_path: Annotated[AbsolutePath, Column]


ProjectState = Literal["present", "unreadable", "missing", "unknown"]
"""Where a listed project stands; ``unknown`` is a record that can't be read."""


class RecordedProject(Record, kind="untaped.recorded_project"):
    """One project the home records (``project list``).

    ``path`` is its folder; ``holds`` the items it holds, per ``plugin.store``.
    An ``unknown`` row is a record untaped can't read: no ``id``, its file as
    ``path`` and why in ``detail``.
    """

    name: Annotated[str, Column]
    path: Annotated[str, Column]
    state: Annotated[ProjectState, Column]
    current: Annotated[bool, Column] = Field(description="Whether the working directory is in it.")
    id: _Omitted = None
    holds: dict[str, list[str]] = Field(default_factory=dict)
    detail: _Omitted = None


class ProjectOutcome(OutcomeRecord, kind="untaped.project_outcome"):
    """What ``project init`` did to one folder.

    ``action`` is ``created``, ``unchanged``, ``recorded`` (the home now
    lists it), ``repaired`` (its record follows a move) or ``new id`` (a copy
    given its own).
    """

    name: Annotated[str, Column]
    path: Annotated[str, Column]
    id: str
    detail: Annotated[str, Column]


SetupState = Literal["done", "todo", "failed", "skipped"]
"""Where a ``setup plan`` step stands."""

SetupActor = Literal["agent", "user"]
"""Who takes a ``setup plan`` step."""


class SetupStep(Record, kind="untaped.setup_step"):
    """One step of ``setup plan``; ``run`` is the command that does it, as arguments."""

    step: Annotated[str, Column]
    plugin: str
    state: Annotated[SetupState, Column]
    detail: Annotated[str, Column]
    run: Annotated[list[str], Column] = Field(
        description=(
            "The complete argv to pass after `untaped`; a `<NAME>` token is a value to ask for."
        )
    )
    by: Annotated[SetupActor, Column] = Field(
        description="Whose step it is: the user's when a secret would pass through the agent."
    )


class PluginCheckRow(CheckRecord, kind="untaped.plugin_check"):
    """One finding of a plugin check (:func:`untaped.sdk.testing.check_plugin`)."""

    plugin: Annotated[str, Column]
    check: Annotated[str, Column] = Field(
        description=("`conventions`, `registration`, `conformance`, `live` or `contracts`.")
    )
    title: Annotated[str, Column] = Field(
        description=(
            "What it is about: a convention rule, `owner.contract` or `owner.contract.method`."
        )
    )
    detail: Annotated[str, Column] = ""


class ScaffoldOutcome(OutcomeRecord, TargetRecord, kind="untaped.scaffold_outcome"):
    """One file ``plugin new`` wrote (``created``) or would write (``planned``)."""


class Skill(Record, kind="untaped.skill"):
    """A stub skill the installed plugins ship (``skills list``)."""

    name: str
    description: str


class InstalledSkill(TargetRecord, kind="untaped.installed_skill"):
    """An untaped-installed skill directory and whether it matches this version."""

    name: Annotated[str, Column]
    target: Annotated[str, Column]
    scope: Annotated[str, Column]
    state: Annotated[str, Column]
    target_path: Annotated[AbsolutePath, Column]


class SkillOutcome(OutcomeRecord, TargetRecord, kind="untaped.skill_outcome"):
    """What ``skills update`` or ``skills remove`` did to one installed skill."""

    name: Annotated[str, Column]
    target: Annotated[str, Column]
    scope: Annotated[str, Column]
    target_path: Annotated[AbsolutePath, Column]
    action: Annotated[str, Column]
    error: _RowError = None
