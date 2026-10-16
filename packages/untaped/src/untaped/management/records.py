"""The records the root management commands write, each declaring its ``untaped.*`` kind.

They live in one module that building the root app imports, so every kind
core declares is registered before any command runs (``plugin schema``
finds each one). A command either builds these models where it makes a
row or, where other code reads its rows as dicts (doctor's rows feed
``doctor fix``, setup's the setup screen), validates them at emit. Field
order and which fields an output omits match the dict rows these replaced.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field

from untaped.output.diagnostic import _is_none, _Omitted
from untaped.records import (
    AbsolutePath,
    CheckRecord,
    CheckStatus,
    OutcomeRecord,
    Record,
    TargetRecord,
    _RowError,
)


class AuthOutcome(OutcomeRecord, kind="untaped.auth_outcome"):
    """What ``auth set``, ``auth unset`` or ``auth migrate`` did to one section's token.

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
    """Where one profile's token for one section comes from (``auth status``)."""

    profile: str
    section: str
    source: str


class ConfigMigrationOutcome(OutcomeRecord, kind="untaped.config_migration_outcome"):
    """One key ``config migrate`` renamed, dropped or deleted in one profile."""

    table_columns: ClassVar[tuple[str, ...]] = ("profile", "from", "to", "action", "detail")

    model_config = ConfigDict(serialize_by_alias=True)

    profile: str
    from_: str = Field(alias="from")
    to: str
    """Empty for a deleted key."""
    action: str
    detail: str
    """For a deleted key, the value it held and why it went."""


class DoctorCheckRow(CheckRecord, kind="untaped.doctor_check"):
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
    automatic: bool
    """Whether ``doctor fix`` runs ``fix`` on its own."""


class FixOutcome(OutcomeRecord, kind="untaped.fix_outcome"):
    """What one fix ``doctor fix`` ran (or would run) did to the checks it covers."""

    fix: list[str]
    checks: list[str]
    action: str
    detail: str
    error: _RowError = None


class MigrationPreviewRow(Record, kind="untaped.migration_row"):
    """One line of ``setup migrate-dirs``' preview, under its migration's ``id``.

    A migration that cannot run is one ``failed`` row with ``error`` and no
    paths.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("id", "action", "source", "destination", "detail")

    id: str
    action: str
    source: _Omitted = None
    destination: _Omitted = None
    detail: str = ""
    bytes: Annotated[int | None, Field(exclude_if=_is_none)] = None
    error: _RowError = None


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
    run: list[str]
    by: SetupActor
    """Whose step it is: the user's when a secret would pass through the agent."""


class PluginCheckRow(CheckRecord, kind="untaped.plugin_check"):
    """One finding of ``untaped plugin check``."""

    table_columns: ClassVar[tuple[str, ...]] = ("plugin", "check", "title", "status", "detail")

    plugin: str
    check: str
    """``conventions``, ``registration``, ``conformance``, ``live``, ``fills``, ``schema``,
    ``contracts`` or ``migrations``."""
    title: str
    """What it is about: a convention rule, ``owner.contract`` or ``owner.contract.method``."""
    detail: str = ""


class ScaffoldOutcome(OutcomeRecord, TargetRecord, kind="untaped.scaffold_outcome"):
    """One file ``plugin new`` wrote (``created``) or would write (``planned``)."""

    table_columns: ClassVar[tuple[str, ...]] = ("action", "target_path")


class SkillRow(Record, kind="untaped.skill"):
    """A skill the installed plugins ship (``skills list``)."""

    name: str
    description: str
    source: str


class InstalledSkillRow(TargetRecord, kind="untaped.installed_skill"):
    """An untaped-installed skill directory and whether it matches this version."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "target", "scope", "state", "target_path")

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
