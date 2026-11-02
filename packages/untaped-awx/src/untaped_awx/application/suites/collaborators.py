"""What a suite run is given: what it launches with, what it reads with, and its options.

:class:`~untaped_awx.application.suites.runner.RunTestSuite` takes one of
each. :class:`SuiteLaunching` is everything that submits, decides or waits;
:class:`SuiteReading` everything that reads an execution back;
:class:`SuiteOptions` how much the run reports and how it keeps time. The
readers, the launcher, the watcher, the approver and the preflight have the
types of their ports (:mod:`untaped_awx.application.suites.ports`), so the
command builds them from the AWX client it holds.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from untaped_awx.application.jobs.ports import Canceller
from untaped_awx.application.suites.ports import (
    ApprovalDecider,
    EventReader,
    FkPrefetcher,
    HostReader,
    JobReader,
    LaunchCheck,
    Launcher,
    LogReader,
    NodeReader,
    TailReader,
    Watcher,
)
from untaped_awx.application.suites.resolver import ResolveCasePayload
from untaped_awx.domain import Job, ResourceSpec
from untaped_awx.domain.suites.case_failure import CaseFailure


@dataclass(frozen=True)
class SuiteLaunching:
    """What submits a case, decides on it while it runs, or waits for it.

    ``specs`` gives the spec of a template kind (``JobTemplate``,
    ``WorkflowJobTemplate``); ``jt_scope`` the scope a suite's template is
    looked up in by default. ``approver`` answers a workflow's approvals.
    Without ``preflight`` no launch is checked first; without ``canceller``
    what the run stops watching is left running. ``stop`` is set on Ctrl-C:
    workers stop polling and queued cases never launch.
    """

    resolver: ResolveCasePayload
    launcher: Launcher
    watcher: Watcher
    specs: Callable[[str], ResourceSpec]
    fk_prefetcher: FkPrefetcher
    approver: ApprovalDecider
    preflight: LaunchCheck | None = None
    canceller: Canceller | None = None
    jt_scope: dict[str, str] | None = None
    stop: threading.Event | None = None


@dataclass(frozen=True)
class SuiteReading:
    """What reads an execution back: the job, its log, events, hosts and nodes.

    Most reads come once it ended; a workflow's nodes are also read while it
    runs (to answer its approvals), and a job whose cancel was refused is read
    again. ``job_url`` gives the execution's page in AWX's web UI, when known.
    """

    job_reader: JobReader
    log_reader: LogReader
    event_reader: EventReader
    tail_reader: TailReader
    host_reader: HostReader
    node_reader: NodeReader
    job_url: Callable[[Job], str | None]


@dataclass(frozen=True)
class SuiteOptions:
    """How much the run reports, and how it keeps time.

    ``report`` takes a failure (with its ``suite/case``) that counts toward
    the exit code when the run aborts. ``evidence`` attaches the failed tasks
    and log tail to the cases that did not pass; ``hosts`` reports every
    case's host summaries (a failed job's are read anyway).
    """

    report: Callable[[str | None, CaseFailure], object]
    evidence: bool = True
    hosts: bool = False
    clock: Callable[[], float] = time.monotonic
