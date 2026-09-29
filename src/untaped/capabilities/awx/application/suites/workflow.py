"""WorkflowRun: what one workflow case reads of its workflow job, and the approvals it answers.

A :class:`WorkflowRun` belongs to one launched workflow job (one case's run,
never shared between the run's workers). It reads the nodes of the workflow
job and of the workflows nested in it (once each has ended; afresh while it
runs), each node's finished job once (:class:`NodeRun`, with what checking it
read, so a node that is both checked and blamed is read once), and every host
summary record of each node job once, summed for a workflow. :meth:`node_jobs`
is the one walk over the nodes, nested ones included, :data:`MAX_NESTING`
levels deep. While the workflow runs, :meth:`WorkflowRun.answer` approves or
denies the approvals it waits on as the case says, or raises
:class:`PendingApprovalError` when the case says nothing.

:class:`JobRead` is what checking one execution read (a job case's, or a
node job's), reused as its evidence.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from untaped.capabilities.awx.application.suites.ports import (
    ApprovalDecider,
    HostReader,
    JobReader,
    NodeReader,
)
from untaped.capabilities.awx.domain import Job
from untaped.capabilities.awx.domain.case_failure import (
    APPROVALS_HINT,
    EXPECTATION,
    SUITE,
    CaseFailure,
    FailedTask,
    failure,
)
from untaped.capabilities.awx.domain.job import HostSummary
from untaped.capabilities.awx.domain.suite import Approvals, ExpectationResult, NodeExpectation
from untaped.capabilities.awx.domain.workflow_run import (
    APPROVAL,
    MAX_NESTING,
    WORKFLOW_JOB,
    RunNode,
    summed_host_records,
)
from untaped.capabilities.awx.errors import PendingApprovalError
from untaped.capability_api import ErrorCategory, ErrorInfo, q

_PLAYBOOK_KINDS = frozenset({"job"})
"""Execution types that run a playbook: they have host summaries, changed tasks, a commit."""


@dataclass(slots=True)
class JobRead:
    """What checking one execution read, reused as its evidence and by its next check."""

    source: Job
    """The responsible execution: the failed update the job names, else the job."""
    workflow: WorkflowRun | None = None
    """The workflow run the execution belongs to (``None``: a job case's job)."""
    log: list[str] | None = None
    """The job's whole log, when a log expectation downloaded it."""
    tasks: tuple[FailedTask, ...] | None = None
    tasks_read: bool = False
    update: Job | None = None
    update_read: bool = False
    hosts_error: Exception | None = None
    """Why the host summaries could not be read."""
    all_hosts: dict[str, HostSummary] | None = None
    """Every host's summary, when every record was read (a workflow's, a node job's)."""
    tails: dict[tuple[str, int], tuple[str, ...] | None] = field(default_factory=dict)
    """Log tails read, by execution."""


@dataclass(slots=True)
class NodeRun:
    """A node's finished job, what checking it read, and the row fields that set."""

    job: Job
    read: JobRead
    fields: dict[str, Any] = field(default_factory=dict)


class WorkflowRun:
    """The reads and approvals of one workflow case's run (see the module docstring)."""

    def __init__(
        self,
        *,
        node_reader: NodeReader,
        approver: ApprovalDecider,
        job_reader: JobReader,
        host_reader: HostReader,
        approvals: Approvals | None,
    ) -> None:
        self._read_nodes = node_reader
        self._decide = approver
        self._reader = job_reader
        self._read_hosts = host_reader
        self._approvals = approvals
        self._nodes: dict[int, list[RunNode]] = {}
        self._runs: dict[int, NodeRun] = {}
        self._host_records: dict[int, Sequence[Mapping[str, Any]]] = {}
        self.answered: dict[int, Approvals] = {}
        """The approvals this run answered, and how."""

    def nodes(self, workflow: Job) -> list[RunNode]:
        """A workflow job's nodes as they ran (a finished one's are read once)."""
        cached = self._nodes.get(workflow.id)
        if cached is not None:
            return cached
        nodes = [RunNode.from_record(record) for record in self._read_nodes(workflow)]
        if workflow.is_terminal:
            self._nodes[workflow.id] = nodes
        return nodes

    def node_jobs(
        self, workflow: Job, *, depth: int = 0, prefix: str = ""
    ) -> Iterator[tuple[str, RunNode, Job]]:
        """Every node that started an execution, with its path (``outer/inner``) and execution.

        The nodes of a nested workflow follow the node that runs it, up to
        :data:`MAX_NESTING` levels deep.
        """
        for node in self.nodes(workflow):
            execution = node.execution
            if execution is None:
                continue
            path = f"{prefix}{node.label}"
            yield path, node, execution
            if node.kind == WORKFLOW_JOB and depth < MAX_NESTING:
                yield from self.node_jobs(execution, depth=depth + 1, prefix=f"{path}/")

    def run(self, node: RunNode) -> NodeRun:
        """The finished job a node ran, read once."""
        execution = node.execution
        assert execution is not None  # callers check the node ran
        run = self._runs.get(execution.id)
        if run is None:
            job = self._reader.settled(self._reader.fetch(execution))
            run = self._runs[execution.id] = NodeRun(job, JobRead(source=job, workflow=self))
        return run

    def host_records(self, job: Job) -> Sequence[Mapping[str, Any]]:
        """Every host summary record of a node job, read once; a workflow's, summed per host."""
        if job.kind == WORKFLOW_JOB:
            return summed_host_records(map(self.host_records, self.playbook_jobs(job)))
        if job.id not in self._host_records:
            self._host_records[job.id] = list(self._read_hosts(job))
        return self._host_records[job.id]

    def playbook_jobs(self, workflow: Job) -> Iterator[Job]:
        """The playbook jobs of a workflow's nodes, nested workflows' included."""
        for _, _, execution in self.node_jobs(workflow):
            if execution.kind in _PLAYBOOK_KINDS:
                yield execution

    def answer(self, workflow: Job) -> None:
        """Approve or deny each approval the running ``workflow`` waits on, as the case says.

        A node read that may pass when retried is left to the next poll.
        Raises :class:`PendingApprovalError` when the case gives no answer.
        """
        try:
            waiting = [
                (path, node, execution)
                for path, node, execution in self.node_jobs(workflow)
                if node.kind == APPROVAL and node.status == "pending"
            ]
        except Exception as exc:
            if ErrorInfo.from_exception(exc).retryable:
                return
            raise
        for path, node, execution in waiting:
            if execution.id in self.answered:
                continue
            if self._approvals is None:
                raise PendingApprovalError(
                    f"node {path}: approval {q(node.template or execution.id)} is waiting, "
                    "and the case sets no approvals",
                    node=path,
                    approval_id=execution.id,
                    hint=APPROVALS_HINT,
                )
            self._decide(execution.id, approve=self._approvals == "approve")
            self.answered[execution.id] = self._approvals

    def denied(self, approval_id: int) -> bool:
        """Whether this run denied the approval."""
        return self.answered.get(approval_id) == "deny"

    def revision(self, workflow: Job) -> str | None:
        """The commit every playbook job of the workflow ran (``None``: several, or unknown)."""
        try:
            revisions = {
                self._reader.fetch(job).scm_revision or None for job in self.playbook_jobs(workflow)
            }
        except Exception:
            return None
        return next(iter(revisions)) if len(revisions) == 1 else None

    def stalled(self, workflow: Job) -> tuple[str, RunNode] | None:
        """The first node still unfinished, deepest first, with its path."""
        unfinished = [
            (path, node)
            for path, node, execution in self.node_jobs(workflow)
            if not execution.is_terminal
        ]
        leaves = [(path, node) for path, node in unfinished if node.kind != WORKFLOW_JOB]
        found = leaves or unfinished
        return found[0] if found else None


def status_only_failure(
    node: RunNode | None, expect: NodeExpectation, check: ExpectationResult
) -> CaseFailure | None:
    """The failure of a node with only a status: one that never ran, an approval, …"""
    if node is not None and node.job_id is not None and expect.checks_beyond_status:
        message = f"a {node.kind} node has only a status to check"
        return failure(SUITE, ErrorCategory.INVALID, message)
    if check.passed:
        return None
    return failure(EXPECTATION, ErrorCategory.FAILED, check.describe_failure())


__all__ = ["JobRead", "NodeRun", "WorkflowRun", "status_only_failure"]
