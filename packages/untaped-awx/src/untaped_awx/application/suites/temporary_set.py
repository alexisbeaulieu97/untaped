"""TemporarySets: check, provision and tear down the copies of an ``awx test`` run.

A run with ``--source-ref`` provisions the :class:`TemporarySet` the domain
planned, runs its cases, then tears the copies down (``awx test prune`` finds
the ones a killed run left). Every step keeps the attribution of what failed,
so a provisioning failure never reads as a test failure:

- :func:`unpinned_problems` refuses every template the run does not copy
  that would not run the commit: the suite's own, and every job template or
  workflow a workflow of the run runs (nested ones too), that does not prompt
  for ``scm_branch``.
- :meth:`TemporarySets.check` does everything else but the writes: it
  prepares the copies as ``apply`` would (every link resolved by name, never
  created; a missing one fails with the closest names), refuses a copy whose
  name is already taken in its organization, and a job template whose project
  does not allow branch override (``awx.scm``).
- :meth:`TemporarySets.provision` creates them, job templates first (the
  apply engine orders them); a failure, or a copy AWX did not store as
  written, raises before any case launches.
- :meth:`TemporarySets.teardown` finds the run's copies by their marker and
  deletes them, workflows first, retrying while AWX still counts a cancelled
  job as running (409), within one deadline. What it cannot delete (or a
  second Ctrl-C leaves) is a ``failed`` row, never an error: the cases'
  results stand.
- :func:`preflight_copy` checks a case against a copy's spec (survey
  variables, node ids) before the copy exists, for ``validate``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from untaped.sdk import (
    ConfigError,
    ErrorCategory,
    ErrorInfo,
    UntapedError,
    q,
)
from untaped_awx.application.apply_file import prepare_documents
from untaped_awx.application.mutation_engine import BatchMutationEngine
from untaped_awx.application.mutation_types import MutationPlan
from untaped_awx.application.ports import Catalog, FkResolver, ResourceClient
from untaped_awx.application.prepare_actions import extra_var_names
from untaped_awx.application.suites.preflight import PreflightLaunch, refused
from untaped_awx.domain import ResourceSpec
from untaped_awx.domain.case_failure import SCM
from untaped_awx.domain.outcomes import TemporaryCopyOutcome
from untaped_awx.domain.suite import (
    JOB_TEMPLATE,
    WORKFLOW_TEMPLATE,
    TemplateBinding,
)
from untaped_awx.domain.temporary_set import (
    TAG,
    TEMPLATE_KINDS,
    Marker,
    TemporarySet,
    TemporaryTemplate,
    leftover,
    source_name,
)
from untaped_awx.domain.workflow_run import MAX_NESTING, WORKFLOW_JOB
from untaped_awx.errors import (
    AwxError,
    BadRequestError,
    ConflictError,
    LaunchPromptError,
    ResourceNotFoundError,
)

TEARDOWN_TIMEOUT = 30.0
"""Seconds a teardown (or one prune delete) keeps retrying deletes AWX refuses with 409."""
TEARDOWN_DELAY = 3.0
"""Seconds between those retries."""

_PERMISSION_HINT = (
    "the AWX user needs to create and delete job templates and workflows in the organization "
    "(its Job Template Admin and Workflow Admin roles)"
)
_UNPINNED_HINT = "add its spec under `.untaped/awx/` or enable `ask_scm_branch_on_launch`"
_NODE_KINDS = {"job": JOB_TEMPLATE, WORKFLOW_JOB: WORKFLOW_TEMPLATE}
"""The execution a node starts → the kind of template it runs."""


@dataclass(frozen=True)
class Refusal:
    """Why a suite cannot run at the commit: ``label`` names the suite (and node)."""

    suite: str
    label: str
    error: UntapedError


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._client = client
        self._catalog = catalog
        self._fk = fk
        self._sleep = sleep
        self._clock = clock

    def check(self, temp: TemporarySet, refusals: Sequence[Refusal] = ()) -> MutationPlan:
        """The plan creating every copy, after every check a write would fail; nothing is written.

        ``refusals`` (:func:`unpinned_problems`) are refused with the set's own problems.
        """
        problems = [(refusal.label, refusal.error) for refusal in refusals]
        docs = [template.document for template in temp.templates]
        try:
            plan = prepare_documents(self._engine, docs, catalog=self._catalog, fk=self._fk)
        except UntapedError as exc:
            paths = ", ".join(template.path for template in temp.templates)
            raise self._refused(temp, [*problems, (paths, exc)]) from exc
        overrides: dict[Any, UntapedError | None] = {}
        for operation in plan.operations:
            label = f"{operation.spec.kind} {q(operation.resource.metadata.name)}"
            if not operation.create:
                taken = ConflictError(
                    "already exists in its organization; run again for another name",
                    hint=f"`untaped awx test prune --run {temp.marker.run_id} --older-than 0` "
                    "deletes it if a killed run left it",
                )
                problems.append((label, taken))
            elif operation.spec.kind == JOB_TEMPLATE:
                project = operation.payload.get("project")
                if project not in overrides:
                    overrides[project] = self._branch_override(project, temp.marker)
                if (problem := overrides[project]) is not None:
                    problems.append((label, problem))
        if problems:
            raise self._refused(temp, problems)
        return plan

    def provision(self, temp: TemporarySet, plan: MutationPlan) -> None:
        """Create every copy; raise, naming each copy that failed, when one was not created.

        A copy AWX did not store as its spec asks (the engine could not verify
        it) is a failure too: its cases would not test the spec.
        """
        result = self._engine.execute(plan)
        problems: list[tuple[str, UntapedError]] = []
        for outcome in result.outcomes:
            label = f"{outcome.kind} {q(outcome.name)}"
            if outcome.error is not None:
                problems.append((label, _error(outcome.error)))
            elif outcome.action in {"failed", "partial", "conflict"}:
                problems.append((label, AwxError(outcome.detail or outcome.action)))
            elif outcome.unverified:
                unverified = BadRequestError(
                    f"AWX did not store it as the spec asks ({outcome.detail})",
                    hint="rerun with --keep to inspect the copy, then fix the spec",
                )
                problems.append((label, unverified))
        if problems:
            raise self._refused(temp, problems, created=True)

    def leftovers(self, *, run_id: str | None = None) -> list[Leftover]:
        """Every copy AWX holds (only run ``run_id``'s, when given), in deletion order."""
        needle = f"[{TAG} " if run_id is None else f" {run_id}]"
        found: list[Leftover] = []
        for kind in reversed(TEMPLATE_KINDS):  # a workflow before the templates it runs
            spec = self._catalog.get(kind)
            copies = []
            for record in self._client.list(spec, params={"name__contains": needle}):
                marker = leftover(str(record.get("name", "")), str(record.get("description", "")))
                if marker is None or run_id not in (None, marker.run_id):
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

    def delete(self, copy: Leftover, *, deadline: float | None = None) -> None:
        """Delete one copy; a copy already gone counts as deleted.

        AWX refuses (409) while a job of the copy is still running, which a
        job cancelled a moment ago may still be: retried until ``deadline``
        (a :data:`TEARDOWN_TIMEOUT` from now by default).
        """
        spec = self._catalog.get(copy.kind)
        if deadline is None:
            deadline = self._clock() + TEARDOWN_TIMEOUT
        while True:
            try:
                self._client.delete(spec, copy.id)
                return
            except ResourceNotFoundError:
                return
            except ConflictError:
                if self._clock() + TEARDOWN_DELAY > deadline:
                    raise
                self._sleep(TEARDOWN_DELAY)

    def teardown(self, run: Marker, *, keep: bool = False) -> list[TemporaryCopyOutcome]:
        """Delete (or with ``keep`` only list) the run's copies; failures become ``failed`` rows.

        Nothing raises, a second Ctrl-C included: a copy teardown could not
        find or delete is left for ``awx test prune`` and never changes a
        case's result. Every delete shares one :data:`TEARDOWN_TIMEOUT`.
        """
        try:
            copies = self.leftovers(run_id=run.run_id)
        except (Exception, KeyboardInterrupt) as exc:
            return [_unlisted(run, exc)]
        if keep:
            return [copy.outcome("kept") for copy in copies]
        deadline = self._clock() + TEARDOWN_TIMEOUT
        rows = []
        for index, copy in enumerate(copies):
            try:
                self.delete(copy, deadline=deadline)
            except KeyboardInterrupt:
                info = _interrupted()
                rows += [
                    left.outcome("failed", detail=info.message, error=info)
                    for left in copies[index:]
                ]
                break
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
        try:
            record = self._client.get(self._catalog.get("Project"), project).model_dump()
        except UntapedError as exc:
            return exc
        if record.get("allow_override"):
            return None
        return ConfigError(
            f"project {q(str(record.get('name')))} does not allow branch override "
            f"(allow_override is false), so the copy cannot run commit {marker.sha}",
            category="invalid",
            system=SCM,
            hint="enable allow_override on the project",
        )

    @staticmethod
    def _refused(
        temp: TemporarySet, problems: Sequence[tuple[str, UntapedError]], *, created: bool = False
    ) -> ConfigError:
        """The error refusing the run: a permission problem names the roles to grant."""
        ref = temp.marker.ref
        if not temp.templates:
            header = f"cannot run {ref}; nothing launched:"
        else:
            what = "the copies created are torn down" if created else "nothing was created"
            header = f"cannot provision the temporary test set of {ref} ({what}); nothing launched:"
        error = refused(header, problems)
        if error.category == ErrorCategory.PERMISSION:
            error.hint = _PERMISSION_HINT
        return error


def unpinned_problems(
    preflight: PreflightLaunch,
    specs: Callable[[str], ResourceSpec],
    bindings: Iterable[tuple[str, TemplateBinding]],
    temp: TemporarySet,
    *,
    ref: str,
    sha: str,
) -> list[Refusal]:
    """Every template the run would launch without the commit, by suite.

    A suite's template, and each job template or workflow a workflow of the
    run runs (nested ones, :data:`MAX_NESTING` deep), runs the commit when the
    run copies it, or when it prompts for ``scm_branch`` (it is passed the
    commit). Any other, and one that cannot be found, is refused before
    anything is created.
    """
    problems: list[Refusal] = []
    for suite, binding in bindings:
        walk = _CommitCheck(preflight, specs, temp, suite, problems, ref=ref, sha=sha)
        copy = temp.bound(binding)
        if copy is not None:
            walk.copied(copy, path="", depth=0)
        else:
            walk.named(binding.kind, binding.name, binding.scope, path="", depth=0)
    return problems


class _CommitCheck:
    """One suite's walk over the templates it runs; what would not run the commit is refused."""

    def __init__(
        self,
        preflight: PreflightLaunch,
        specs: Callable[[str], ResourceSpec],
        temp: TemporarySet,
        suite: str,
        problems: list[Refusal],
        *,
        ref: str,
        sha: str,
    ) -> None:
        self._preflight = preflight
        self._specs = specs
        self._temp = temp
        self._suite = suite
        self._problems = problems
        self._ref = ref
        self._sha = sha

    def copied(self, template: TemporaryTemplate, *, path: str, depth: int) -> None:
        """A copy runs the commit; its nodes' templates must too."""
        if depth >= MAX_NESTING:
            return
        for target in template.targets:
            node = f"{path}{target.node}"
            nested = self._temp.copy_of(target.key)
            if nested is not None:
                self.copied(nested, path=f"{node}/", depth=depth + 1)
            else:
                scope = (
                    None if target.organization is None else {"organization": target.organization}
                )
                self.named(target.kind, target.name, scope, path=node, depth=depth + 1)

    def named(
        self, kind: str, name: str, scope: dict[str, str] | None, *, path: str, depth: int
    ) -> None:
        """A template AWX holds, found by name."""
        spec = self._specs(kind)
        try:
            template, read = self._preflight.template(spec, name=name, scope=scope)
            launch = read("launch")
        except UntapedError as exc:
            self._refuse(path, exc)
            return
        self.held(spec, template.id, template.name or name, launch, path=path, depth=depth)

    def held(
        self,
        spec: ResourceSpec,
        template_id: int,
        name: str,
        launch: Mapping[str, Any],
        *,
        path: str,
        depth: int,
    ) -> None:
        """A template AWX holds must prompt for the branch; a workflow's nodes' templates too."""
        if not launch.get("ask_scm_branch_on_launch"):
            self._refuse(
                path,
                LaunchPromptError(
                    f"{spec.kind} {q(name)} has no spec in the repository at {self._ref} and "
                    f"does not prompt for scm_branch, so it would run its own branch, not "
                    f"commit {self._sha[:7]}",
                    hint=_UNPINNED_HINT,
                    details={"field": "scm_branch"},
                ),
            )
            return
        if spec.kind != WORKFLOW_TEMPLATE or depth >= MAX_NESTING:
            return
        try:
            nodes = self._preflight.nodes(spec, template_id)
            for node in nodes:
                kind = _NODE_KINDS.get(node.kind or "")
                if kind is None or node.template_id is None:
                    continue
                node_spec = self._specs(kind)
                self.held(
                    node_spec,
                    node.template_id,
                    node.template or f"#{node.template_id}",
                    self._preflight.launch_of(node_spec, node.template_id),
                    path=f"{path}/{node.label}" if path else node.label,
                    depth=depth + 1,
                )
        except UntapedError as exc:
            self._refuse(path, exc)

    def _refuse(self, path: str, error: UntapedError) -> None:
        label = self._suite if not path else f"{self._suite} (node {path})"
        self._problems.append(Refusal(self._suite, label, error))


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
    known = [node.id for node in template.nodes]
    unknown = sorted(set(nodes) - set(known))
    if unknown:
        raise ResourceNotFoundError(
            "workflow node",
            {"name": unknown[0], "workflow": template.name},
            candidates=known,
            status=None,
        )


def _error(info: ErrorInfo) -> UntapedError:
    """A failed row's error as one to raise, keeping its attribution."""
    return UntapedError(info.message, category=info.category, system=info.system, hint=info.hint)


def _interrupted() -> ErrorInfo:
    return ErrorInfo(
        category=ErrorCategory.INTERRUPTED,
        system="untaped",
        retryable=False,
        message="teardown interrupted",
    )


def _unlisted(run: Marker, exc: BaseException) -> TemporaryCopyOutcome:
    """The row of a teardown that could not even list the run's copies."""
    info = _interrupted() if isinstance(exc, KeyboardInterrupt) else ErrorInfo.from_exception(exc)
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
    "TEARDOWN_DELAY",
    "TEARDOWN_TIMEOUT",
    "Leftover",
    "Refusal",
    "TemporarySets",
    "planned_outcome",
    "preflight_copy",
    "unpinned_problems",
]
