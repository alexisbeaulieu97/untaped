"""TemporarySets: check, provision and tear down the copies of an ``awx test`` run.

A run with ``--source-ref`` provisions the :class:`TemporarySet` the domain
planned, runs its cases, then tears the copies down (``awx test prune`` finds
the ones a killed run left). Every step keeps the attribution of what failed,
so a provisioning failure never reads as a test failure:

- :meth:`TemporarySets.check` does everything but the writes: it prepares the
  copies as ``apply`` would (every link resolved by name, never created; a
  missing one fails with the closest names), refuses a copy whose name is
  already taken in its organization, and a job template whose project does not
  allow branch override (``awx.scm``).
- :meth:`TemporarySets.provision` creates them, job templates first (the
  apply engine orders them); a failure raises before any case launches.
- :meth:`TemporarySets.teardown` finds the run's copies by their marker and
  deletes them, workflows first, retrying while AWX still counts a cancelled
  job as running (409). A copy it cannot delete is a ``failed`` row, never an
  error: the cases' results stand.
- :func:`unpinned_problems` refuses a template the run does not copy and that
  does not prompt for ``scm_branch``: it would run its own branch, not the
  commit under test.
- :func:`preflight_copy` checks a case against a copy's spec (survey
  variables, node ids) before the copy exists, for ``validate``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from untaped.capabilities.awx.application.apply_file import prepare_documents
from untaped.capabilities.awx.application.mutation_engine import BatchMutationEngine
from untaped.capabilities.awx.application.mutation_types import MutationPlan
from untaped.capabilities.awx.application.ports import Catalog, FkResolver, ResourceClient
from untaped.capabilities.awx.application.prepare_actions import extra_var_names
from untaped.capabilities.awx.application.suites.preflight import PreflightLaunch
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.domain.case_failure import SCM, failure_system
from untaped.capabilities.awx.domain.outcomes import TemporaryCopyOutcome
from untaped.capabilities.awx.domain.suite import JOB_TEMPLATE, TemplateBinding
from untaped.capabilities.awx.domain.temporary_set import (
    TAG,
    TEMPLATE_KINDS,
    Marker,
    TemporarySet,
    TemporaryTemplate,
    leftover,
    source_name,
)
from untaped.capabilities.awx.errors import (
    ConflictError,
    LaunchPromptError,
    ResourceNotFoundError,
)
from untaped.capability_api import (
    ConfigError,
    ErrorCategory,
    ErrorInfo,
    UntapedError,
    attribution,
    most_severe,
    q,
)

TEARDOWN_ATTEMPTS = 10
"""Deletes of one copy AWX refuses with 409 (a cancelled job still running) before giving up."""
TEARDOWN_DELAY = 3.0
"""Seconds between those attempts."""

_PERMISSION_HINT = (
    "the AWX user needs to create and delete job templates and workflows in the organization "
    "(its Job Template Admin and Workflow Admin roles)"
)
_UNPINNED_HINT = "add its spec to `.untaped/awx/templates/` or enable `ask_scm_branch_on_launch`"


@dataclass(frozen=True)
class Leftover:
    """A copy of a temporary set that exists in AWX, found by its name and marker."""

    kind: str
    id: int
    name: str
    organization: str | None
    marker: Marker

    def outcome(self, action: str, **fields: Any) -> TemporaryCopyOutcome:
        """Its row, with ``action`` (and ``detail``/``error``)."""
        return TemporaryCopyOutcome(
            id=self.id,
            name=self.name,
            kind=self.kind,
            template=source_name(self.name),
            organization=self.organization,
            run_id=self.marker.run_id,
            ref=self.marker.ref,
            sha=self.marker.sha,
            created_at=self.marker.created,
            action=action,
            **fields,
        )


def planned_outcome(template: TemporaryTemplate, marker: Marker) -> TemporaryCopyOutcome:
    """The ``planned`` row of a copy a run would create."""
    return TemporaryCopyOutcome(
        name=template.name,
        kind=template.kind,
        template=template.source,
        organization=template.organization,
        run_id=marker.run_id,
        ref=marker.ref,
        sha=marker.sha,
        created_at=marker.created,
        path=template.path,
        prompts=list(template.prompts),
        action="planned",
    )


class TemporarySets:
    """Check, provision, find and delete the copies of temporary test sets."""

    def __init__(
        self,
        *,
        engine: BatchMutationEngine,
        client: ResourceClient,
        catalog: Catalog,
        fk: FkResolver,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._engine = engine
        self._client = client
        self._catalog = catalog
        self._fk = fk
        self._sleep = sleep

    def check(
        self, temp: TemporarySet, problems: Sequence[tuple[str, UntapedError]] = ()
    ) -> MutationPlan:
        """The plan creating every copy, after every check a write would fail; nothing is written.

        ``problems`` (such as :func:`unpinned_problems`) are refused with the set's own.
        """
        docs = [template.document for template in temp.templates]
        try:
            plan = prepare_documents(self._engine, docs, catalog=self._catalog, fk=self._fk)
        except UntapedError as exc:
            paths = ", ".join(template.path for template in temp.templates)
            raise _refused([*problems, (paths, exc)]) from exc
        problems = list(problems)
        overrides: dict[Any, UntapedError | None] = {}
        for operation in plan.operations:
            label = f"{operation.spec.kind} {q(operation.resource.metadata.name)}"
            if not operation.create:
                taken = ConflictError(
                    "already exists in its organization; run again for another name, or delete "
                    "it (`untaped awx test prune` removes leftover copies)"
                )
                problems.append((label, taken))
            elif operation.spec.kind == JOB_TEMPLATE:
                project = operation.payload.get("project")
                if project not in overrides:
                    overrides[project] = self._branch_override(project, temp.marker)
                if (refused := overrides[project]) is not None:
                    problems.append((label, refused))
        if problems:
            raise _refused(problems)
        return plan

    def provision(self, plan: MutationPlan) -> None:
        """Create every copy; raise, naming the copy that failed, when one could not be."""
        result = self._engine.execute(plan)
        failed = [
            outcome
            for outcome in result.outcomes
            if outcome.action in {"failed", "partial", "conflict", "skipped"}
        ]
        if failed:
            first = next((outcome for outcome in failed if outcome.error is not None), failed[0])
            error = first.error or ErrorInfo(
                category=ErrorCategory.FAILED,
                system="awx",
                retryable=False,
                message=first.detail or first.action,
            )
            raise _refused([(f"{first.kind} {q(first.name)}", error)], created=True)

    def leftovers(self, *, run: Marker | None = None) -> list[Leftover]:
        """Every copy AWX holds (of ``run`` only, when given), in the order to delete them."""
        needle = f"[{TAG} " if run is None else f"[{TAG} {run.sha} {run.run_id}]"
        found: list[Leftover] = []
        for kind in reversed(TEMPLATE_KINDS):  # a workflow before the templates it runs
            spec = self._catalog.get(kind)
            copies = []
            for record in self._client.list(spec, params={"name__contains": needle}):
                marker = leftover(str(record.get("name", "")), str(record.get("description", "")))
                if marker is None or (run is not None and marker.run_id != run.run_id):
                    continue
                organization = (record.get("summary_fields") or {}).get("organization") or {}
                copies.append(
                    Leftover(
                        kind=kind,
                        id=int(record["id"]),
                        name=str(record["name"]),
                        organization=organization.get("name"),
                        marker=marker,
                    )
                )
            found += sorted(copies, key=lambda copy: -copy.id)  # the newest (outermost) first
        return found

    def delete(self, copy: Leftover) -> None:
        """Delete one copy; a copy already gone counts as deleted.

        AWX refuses (409) while a job of the copy is still running, which a
        job cancelled a moment ago may still be: retried a few times.
        """
        spec = self._catalog.get(copy.kind)
        for attempt in range(1, TEARDOWN_ATTEMPTS + 1):
            try:
                self._client.delete(spec, copy.id)
                return
            except ResourceNotFoundError:
                return
            except ConflictError:
                if attempt == TEARDOWN_ATTEMPTS:
                    raise
                self._sleep(TEARDOWN_DELAY)

    def teardown(self, run: Marker, *, keep: bool = False) -> list[TemporaryCopyOutcome]:
        """Delete (or with ``keep`` only list) the run's copies; failures become ``failed`` rows.

        Nothing raises: a copy teardown could not find or delete is left for
        ``awx test prune``, and never changes a case's result.
        """
        try:
            copies = self.leftovers(run=run)
        except Exception as exc:
            return [_unlisted(run, exc)]
        if keep:
            return [copy.outcome("kept") for copy in copies]
        rows = []
        for copy in copies:
            try:
                self.delete(copy)
            except Exception as exc:
                info = ErrorInfo.from_exception(exc)
                rows.append(copy.outcome("failed", detail=info.message, error=info))
            else:
                rows.append(copy.outcome("deleted"))
        return rows

    def _branch_override(self, project: Any, marker: Marker) -> UntapedError | None:
        """Why a copy's project cannot run the commit (``None``: it can)."""
        if not isinstance(project, int) or isinstance(project, bool):
            return None
        record = self._client.get(self._catalog.get("Project"), project).model_dump()
        if record.get("allow_override"):
            return None
        return ConfigError(
            f"project {q(str(record.get('name')))} does not allow branch override "
            f"(allow_override is false), so the copy cannot run commit {marker.sha}",
            category="invalid",
            system=SCM,
            hint="enable allow_override on the project",
        )


def unpinned_problems(
    preflight: PreflightLaunch,
    specs: Callable[[str], ResourceSpec],
    bindings: Iterable[tuple[str, TemplateBinding]],
    *,
    ref: str,
    sha: str,
) -> list[tuple[str, UntapedError]]:
    """The suites (by name) whose template, not copied, would not run the commit, and why.

    Such a template runs the commit only when it prompts for ``scm_branch``;
    one that does not, or cannot be found, is refused before anything is created.
    """
    problems: list[tuple[str, UntapedError]] = []
    for suite, binding in bindings:
        try:
            _, read = preflight.template(
                specs(binding.kind), name=binding.name, scope=binding.scope
            )
            prompts = bool(read("launch").get("ask_scm_branch_on_launch"))
        except UntapedError as exc:
            problems.append((suite, exc))
            continue
        if not prompts:
            problems.append(
                (
                    suite,
                    LaunchPromptError(
                        f"{binding.kind} {q(binding.name)} has no spec in the repository at "
                        f"{ref} and does not prompt for scm_branch, so it would run its own "
                        f"branch, not commit {sha[:7]}",
                        hint=_UNPINNED_HINT,
                        details={"field": "scm_branch"},
                    ),
                )
            )
    return problems


def preflight_copy(
    template: TemporaryTemplate, payload: Mapping[str, Any], nodes: Collection[str] = ()
) -> None:
    """Raise when a case could not launch a copy that does not exist yet.

    The copy prompts for every field its cases set, so only its survey's
    required variables and, for a workflow, the node ids the case checks
    remain to check, against the spec.
    """
    label = f"{template.kind} {q(template.name)}"
    spec = template.document.spec
    survey = spec.get("survey_spec") if spec.get("survey_enabled") else None
    questions = survey.get("spec") if isinstance(survey, Mapping) else None
    needed = [
        str(question["variable"])
        for question in questions or []
        if isinstance(question, Mapping) and question.get("required") and "variable" in question
    ]
    supplied = extra_var_names(payload.get("extra_vars"))
    missing = [name for name in needed if name not in supplied]
    if missing:
        raise LaunchPromptError(
            f"{label} requires survey variables {', '.join(missing)}; set them in extra_vars"
        )
    if nodes:
        known = [str(node.get("id")) for node in spec.get("nodes") or []]
        unknown = sorted(set(nodes) - set(known))
        if unknown:
            raise ResourceNotFoundError(
                "workflow node",
                {"name": unknown[0], "workflow": template.name},
                candidates=known,
                status=None,
            )


def approval_nodes(template: TemporaryTemplate) -> list[str]:
    """The ids of a workflow copy's approval nodes, from its spec."""
    return [
        str(node["id"]) for node in template.document.spec.get("nodes") or [] if "approval" in node
    ]


def _refused(
    problems: Sequence[tuple[str, UntapedError | ErrorInfo]], *, created: bool = False
) -> ConfigError:
    """One error listing why the set cannot be provisioned, attributed as its worst problem.

    A refusal the suite or its specs can fix is ``awx.suite`` (the project's
    branch override ``awx.scm``); a rejected token or permission is
    ``awx.credentials``, an unavailable controller ``awx.controller``.
    """
    what = "the copies created are torn down" if created else "nothing was created"
    lines = [f"cannot provision the temporary test set ({what}); nothing launched:"]
    lines += [f"  {label}: {_message(problem)}" for label, problem in problems]
    errors = [problem for _, problem in problems if isinstance(problem, UntapedError)]
    worst: UntapedError | ErrorInfo = most_severe(errors) if errors else problems[0][1]
    info = worst if isinstance(worst, ErrorInfo) else ErrorInfo.from_exception(worst)
    fields = attribution(worst) if isinstance(worst, UntapedError) else {"hint": info.hint}
    fields |= {"category": info.category, "system": failure_system(worst, launching=True)}
    if info.category == ErrorCategory.PERMISSION:
        fields["hint"] = _PERMISSION_HINT
    return ConfigError("\n".join(lines), **fields)


def _message(problem: UntapedError | ErrorInfo) -> str:
    return problem.message if isinstance(problem, ErrorInfo) else str(problem)


def _unlisted(run: Marker, exc: Exception) -> TemporaryCopyOutcome:
    """The row of a teardown that could not even list the run's copies."""
    info = ErrorInfo.from_exception(exc)
    return TemporaryCopyOutcome(
        name=f"[{TAG} {run.sha} {run.run_id}]",
        kind="unknown",
        template="unknown",
        run_id=run.run_id,
        ref=run.ref,
        sha=run.sha,
        created_at=run.created,
        action="failed",
        detail=f"could not list the run's copies: {info.message}",
        error=info,
    )


__all__ = [
    "TEARDOWN_ATTEMPTS",
    "TEARDOWN_DELAY",
    "Leftover",
    "TemporarySets",
    "approval_nodes",
    "planned_outcome",
    "preflight_copy",
    "unpinned_problems",
]
