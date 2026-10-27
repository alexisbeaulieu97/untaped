"""The records awx writes, each declaring its ``awx.*`` kind: models only.

Two families:

- **AWX's answer, passed through** (:class:`AwxRecord`): a resource of the
  catalog (one model per ``AwxResourceSpec``, named by the spec's
  ``record``), a job and a unified template. Each types the fields untaped
  reads (``id``, ``name``, ``type``, the organization, the parent, the SCM
  fields on templates) and keeps every other field AWX sent
  (``extra="allow"``), so the row on stdout is AWX's: a field AWX left out
  stays out. A foreign key is ``int | str``: ``--with-names`` (and a table)
  writes its name.
- **Rows awx builds**: job events and log lines, the outcome of each
  mutation and job action, a saved document, a test case. Their field order
  and their fields are the dict rows they replaced, except that a field the
  row used to leave out when unset is now ``null``.

A model a use case builds declares its kind in the domain module it belongs
to (``PingStatus``, ``WorkflowNode``, ``WorkflowUsage``, ``CaseResult``, the
copy and rename outcomes, and ``AwxResources``, the snapshot ``awx lint``'s
rules read, in ``domain/lint/resources.py``).
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import ConfigDict, Field, JsonValue

from untaped.sdk import Duration, ObjectName, OutcomeRecord, Record, UtcTimestamp
from untaped_awx.domain.suites.temporary_set import TemporaryCopyOutcome

ForeignKey = int | str | None
"""An AWX id, or the name ``--with-names`` and a table write in its place."""

# --------------------------------------------------------------------------- #
# AWX's answer
# --------------------------------------------------------------------------- #


class AwxRecord(Record):
    """A row AWX answered: the fields untaped reads, typed, and every other one AWX sent."""

    model_config = ConfigDict(frozen=True, extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)

    id: int


class AwxResource(AwxRecord):
    """A resource of the catalog, as ``list`` and ``get`` write it."""

    name: str
    type: str | None = Field(
        default=None, description="AWX's own spelling of the kind (`job_template`)."
    )


class Organization(AwxResource, kind="awx.organization"):
    """An organization."""


class CredentialType(AwxResource, kind="awx.credential_type"):
    """A credential type (global)."""


class Credential(AwxResource, kind="awx.credential"):
    """A credential; AWX returns its secrets as ``$encrypted$``."""

    organization: ForeignKey = None


class Project(AwxResource, kind="awx.project"):
    """A project: where its playbooks come from."""

    organization: ForeignKey = None
    scm_url: str | None = None
    scm_branch: str | None = None
    scm_revision: Annotated[str | None, ObjectName] = None
    allow_override: bool | None = Field(
        default=None,
        description="Whether a job template may run another branch (its own `scm_branch`).",
    )


class Inventory(AwxResource, kind="awx.inventory"):
    """An inventory."""

    organization: ForeignKey = None


class InventorySource(AwxResource, kind="awx.inventory_source"):
    """An inventory source, under its inventory."""

    inventory: ForeignKey = None


class Host(AwxResource, kind="awx.host"):
    """A host, under its inventory."""

    inventory: ForeignKey = None


class Group(AwxResource, kind="awx.group"):
    """A group, under its inventory."""

    inventory: ForeignKey = None


class ExecutionEnvironment(AwxResource, kind="awx.execution_environment"):
    """An execution environment."""

    organization: ForeignKey = None


class Label(AwxResource, kind="awx.label"):
    """A label."""

    organization: ForeignKey = None


class InstanceGroup(AwxResource, kind="awx.instance_group"):
    """An instance group (global)."""


class SystemJobTemplate(AwxResource, kind="awx.system_job_template"):
    """A management job template, built into AWX."""


class JobTemplate(AwxResource, kind="awx.job_template"):
    """A job template; ``--with-scm`` adds the repository and ref its jobs check out."""

    organization: ForeignKey = None
    project: ForeignKey = None
    scm_branch: str | None = Field(
        default=None,
        description=(
            "The template's own branch, which runs only when its project allows the override."
        ),
    )
    scm_url: str | None = Field(default=None, description="`--with-scm`: the project's repository.")
    effective_scm_ref: str | None = Field(
        default=None,
        description="`--with-scm`: the ref a job checks out (empty: the repository's default).",
    )
    project_allow_override: bool | None = Field(
        default=None,
        description="`--with-scm`: whether the project lets the template pick the branch.",
    )


class WorkflowJobTemplate(AwxResource, kind="awx.workflow_job_template"):
    """A workflow job template."""

    organization: ForeignKey = None


class Schedule(AwxResource, kind="awx.schedule"):
    """A schedule, under the template, project or inventory source it runs."""

    unified_job_template: ForeignKey = None


class JobRecord(AwxRecord, kind="awx.job"):
    """An execution (job, workflow job, project or inventory update) as AWX reports it.

    ``jobs list`` and ``jobs get`` write AWX's whole row (its ``type`` names
    the execution kind); ``jobs wait`` the fields it watched (``kind``).
    """

    name: str | None = None
    status: str | None = None
    elapsed: Annotated[float | None, Duration] = None
    scm_revision: Annotated[str | None, ObjectName] = None


class UnifiedTemplate(AwxRecord, kind="awx.unified_template"):
    """A template, project or inventory source through AWX's unified view."""

    name: str
    type: str | None = None
    scm_revision: Annotated[str | None, ObjectName] = None


# --------------------------------------------------------------------------- #
# Rows awx builds
# --------------------------------------------------------------------------- #


class JobEventRow(Record, kind="awx.event"):
    """One event of a job's run, under the ``job`` it belongs to."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "counter",
        "event",
        "host_name",
        "task",
        "changed",
        "failed",
    )

    job: int
    counter: int
    event: str
    task: str | None
    host: int | None
    host_name: str | None
    role: str | None
    play: str | None
    changed: bool
    failed: bool
    created: str | None
    stdout: str


class JobLogLine(Record, kind="awx.log"):
    """One line of a job's stdout."""

    table_columns: ClassVar[tuple[str, ...]] = ("line",)

    job: int
    line: str


class JobCancelOutcome(OutcomeRecord, kind="awx.cancel_outcome"):
    """One ``jobs cancel`` row: the execution, its status when read, and the result.

    ``action`` is ``planned``, ``skipped`` (already finished),
    ``cancel_requested`` (AWX accepted the request; the job stops
    asynchronously) or ``failed``.
    """

    id: int
    kind: str
    name: str | None = None
    status: str | None = None
    action: str
    detail: str | None = None


class JobRelaunchOutcome(OutcomeRecord, kind="awx.relaunch_outcome"):
    """One ``jobs relaunch`` row: the new execution (``id``) and the job it repeats.

    ``action`` is ``planned``, ``relaunched`` or ``failed``; ``hosts`` is
    ``all`` or ``failed``.
    """

    id: int | None = None
    kind: str
    name: str | None = None
    status: str | None = None
    target_id: int
    hosts: str = "all"
    action: str
    detail: str | None = None


class ActionOutcome(OutcomeRecord):
    """One target of ``launch`` or ``sync``: the execution it started, and what became of it.

    ``id`` and ``kind`` name the execution (``jobs wait --stdin`` reads
    them), ``target_*`` the template, project or source that ran it. The
    execution's fields follow once it was read; ``payload`` is the
    ``--dry-run`` preview of what each target would be sent.
    """

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    table_columns: ClassVar[tuple[str, ...]] = (
        "target_name",
        "id",
        "status",
        "action",
        "detail",
        "payload",
    )

    id: int | None
    kind: str | None
    target_id: int
    target_kind: str
    target_name: str | None
    scope: dict[str, Any]
    action: str
    payload: JsonValue = None
    detail: str | None = None
    name: str | None = None
    status: str | None = None
    started_at: UtcTimestamp | None = None
    finished_at: UtcTimestamp | None = None
    elapsed: Annotated[float | None, Duration] = None
    job_failed: bool | None = Field(
        default=None, alias="failed", description="The execution's own `failed`, as AWX reports it."
    )
    scm_branch: str | None = None
    scm_revision: Annotated[str | None, ObjectName] = None


class LaunchOutcome(ActionOutcome, kind="awx.launch_outcome"):
    """One ``launch`` target."""


class SyncOutcome(ActionOutcome, kind="awx.sync_outcome"):
    """One ``sync`` target."""


class ApplyOutcomeRow(OutcomeRecord, kind="awx.apply_outcome"):
    """What ``apply``, ``patch`` or ``edit`` did to one resource.

    ``fields_changed`` names the fields written (a preserved secret is not
    one); ``identity`` and ``scope`` say which resource it is.
    """

    table_columns: ClassVar[tuple[str, ...]] = (
        "id",
        "name",
        "kind",
        "action",
        "fields_changed",
        "detail",
    )

    id: int | None
    kind: str
    scope: dict[str, Any]
    identity: dict[str, Any]
    partial: bool
    unverified: bool
    name: str
    action: str
    fields_changed: list[str]
    preserved_secrets: list[str]
    dropped_undeclared_secrets: list[str]
    dropped_secrets: list[str]
    detail: str


class DeleteOutcome(OutcomeRecord, kind="awx.delete_outcome"):
    """One resource ``delete`` removed, asked AWX to remove, or skipped."""

    table_columns: ClassVar[tuple[str, ...]] = ("id", "name", "action", "detail")

    id: int
    kind: str
    name: str | None
    scope: dict[str, Any]
    action: str
    detail: str | None = None


class MembershipOutcome(OutcomeRecord, kind="awx.membership_outcome"):
    """What an ``add``/``remove`` of members did to one parent's ``field``."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "id",
        "name",
        "action",
        "associate",
        "disassociate",
        "detail",
    )

    id: int
    kind: str
    name: str | None
    scope: dict[str, Any]
    action: str
    field: str
    associate: list[int]
    disassociate: list[int]
    partial: bool = False
    detail: str | None = None


class Document(Record, kind="awx.document"):
    """A saved resource: the envelope ``save`` writes and ``apply`` reads.

    ``metadata`` is the identity as written, unset keys left out: ``apply``
    reads an ``organization: null`` as the org-less resource, not as unset.
    """

    kind: str
    apiVersion: str
    metadata: dict[str, JsonValue]
    spec: dict[str, Any]


class SuiteCase(Record, kind="awx.test_case"):
    """One case ``test list`` would run, with the suite and template it belongs to."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "suite",
        "case",
        "job_template",
        "workflow_template",
    )

    suite: str
    case: str
    job_template: str | None
    workflow_template: str | None
    organization: str | None
    path: str
    variables: dict[str, dict[str, Any]] = Field(
        description="Each variable the suite declares, as written (unset keys left out)."
    )


class ProvisionOutcome(TemporaryCopyOutcome, kind="awx.provision_outcome"):
    """A temporary copy ``test validate --source-ref`` plans."""

    table_columns: ClassVar[tuple[str, ...]] = ("kind", "name", "template", "prompts", "path")


class PruneOutcome(TemporaryCopyOutcome, kind="awx.prune_outcome"):
    """A temporary copy ``test prune`` deleted, would delete, or failed to."""

    table_columns: ClassVar[tuple[str, ...]] = ("kind", "id", "name", "ref", "created_at", "action")


__all__ = [
    "ActionOutcome",
    "ApplyOutcomeRow",
    "AwxRecord",
    "AwxResource",
    "Credential",
    "CredentialType",
    "DeleteOutcome",
    "Document",
    "ExecutionEnvironment",
    "ForeignKey",
    "Group",
    "Host",
    "InstanceGroup",
    "Inventory",
    "InventorySource",
    "JobCancelOutcome",
    "JobEventRow",
    "JobLogLine",
    "JobRecord",
    "JobRelaunchOutcome",
    "JobTemplate",
    "Label",
    "LaunchOutcome",
    "MembershipOutcome",
    "Organization",
    "Project",
    "ProvisionOutcome",
    "PruneOutcome",
    "Schedule",
    "SuiteCase",
    "SyncOutcome",
    "SystemJobTemplate",
    "UnifiedTemplate",
    "WorkflowJobTemplate",
]
