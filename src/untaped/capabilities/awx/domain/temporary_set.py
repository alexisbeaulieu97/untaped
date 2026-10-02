"""Temporary test sets: repo specs copied, for one ``awx test run``, under unique names.

``awx test run --source-ref REF`` binds each suite by name: a suite whose
``jobTemplate``/``workflowTemplate`` names a template that a spec in the
repository describes (same kind, name and organization) runs against a
temporary copy of that spec, and so do the workflow nodes of a copied
workflow that name another copied template. :func:`plan_temporary_set`
decides the set and each copy's document:

- the name ``NAME [untaped-test SHA7 RUN]`` (:func:`temporary_name`), unique to
  the run;
- the description :class:`Marker` (``untaped-test run=… ref=… sha=…
  created=…``), which ``awx test prune`` finds leftovers by;
- ``scm_branch`` set to the commit, so the copy runs exactly that commit;
- ``ask_<field>_on_launch`` enabled for every field a case sets at launch;
- webhook settings left out, and workflow nodes pointed at the copies.

Everything else in the spec (its links, by name) is applied as written:
nothing it names is ever created. Pure domain: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Self

from untaped.capabilities.awx.domain.envelope import Resource
from untaped.capabilities.awx.domain.launch_prompts import PROMPT_FLAGS
from untaped.capabilities.awx.domain.spec import ResourceSpec
from untaped.capabilities.awx.domain.suite import (
    JOB_TEMPLATE,
    WORKFLOW_TEMPLATE,
    Case,
    Suite,
    TemplateBinding,
)
from untaped.capabilities.awx.domain.workflow_graph import (
    WorkflowNodeSpec,
    dump_workflow_nodes,
    parse_workflow_nodes,
    rename_references,
)
from untaped.capabilities.awx.domain.workflow_run import MAX_NESTING
from untaped.sdk import ConfigError, q

TAG = "untaped-test"
"""What names and marks every temporary copy."""

TEMPLATE_KINDS = (JOB_TEMPLATE, WORKFLOW_TEMPLATE)
"""The kinds a temporary set copies, in creation order."""

WEBHOOK_FIELDS = ("webhook_service", "webhook_credential", "webhook_key")
"""Left out of every copy: a copy must not receive the repository's webhooks."""

_NAME = re.compile(rf" \[{TAG} (?P<sha>[0-9a-f]{{7}}) (?P<run>[a-z0-9]+)\]\Z")
_MARKER = re.compile(
    rf"\A{TAG} run=(?P<run>[a-z0-9]+) ref=(?P<ref>\S+) sha=(?P<sha>[0-9a-f]{{7}}) "
    r"created=(?P<created>\S+)\Z"
)
_AGE = re.compile(r"\A(?P<count>\d+)(?P<unit>[smhd])\Z")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def temporary_name(name: str, sha: str, run_id: str) -> str:
    """``Deploy [untaped-test 1a2b3c4 k3x9]``: the copy of ``name`` for one run at ``sha``."""
    return f"{name} [{TAG} {sha[:7]} {run_id}]"


@dataclass(frozen=True)
class Marker:
    """The description of every copy of one run: which run, ref and commit, and when."""

    run_id: str
    ref: str
    sha: str
    """The commit's first 7 hex digits."""
    created: datetime

    def render(self) -> str:
        created = self.created.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{TAG} run={self.run_id} ref={self.ref} sha={self.sha} created={created}"

    @classmethod
    def parse(cls, description: str) -> Self | None:
        """The marker a description holds (``None``: not a temporary copy's)."""
        found = _MARKER.match(description.strip())
        if found is None:
            return None
        try:
            created = datetime.fromisoformat(found["created"])
        except ValueError:
            return None
        if created.tzinfo is None:
            return None
        return cls(run_id=found["run"], ref=found["ref"], sha=found["sha"], created=created)


def leftover(name: str, description: str) -> Marker | None:
    """The marker of a temporary copy named ``name``; its name and description must agree."""
    named = _NAME.search(name)
    marker = Marker.parse(description)
    if named is None or marker is None:
        return None
    if (named["run"], named["sha"]) != (marker.run_id, marker.sha):
        return None
    return marker


def source_name(name: str) -> str:
    """The name a temporary copy was copied from (``name`` itself for any other)."""
    return _NAME.sub("", name)


def parse_age(text: str) -> timedelta:
    """``2h``, ``30m``, ``1d``, ``90s`` (or ``0``) as a duration."""
    if text == "0":
        return timedelta(0)
    found = _AGE.match(text)
    if found is None:
        raise ValueError(f"{text!r} is not an age like 2h, 30m, 1d or 90s")
    return timedelta(**{_UNITS[found["unit"]]: int(found["count"])})


type _Key = tuple[str, str, str | None]
"""A template's kind, name and organization."""


@dataclass(frozen=True)
class NodeTarget:
    """The template a workflow node runs, by the name and organization its spec gives."""

    node: str
    kind: str
    name: str
    organization: str | None

    @property
    def key(self) -> _Key:
        return (self.kind, self.name, self.organization)


@dataclass(frozen=True)
class TemporaryTemplate:
    """One copy a run creates: the spec it copies and the document it creates."""

    source: str
    """The spec's name: the template the suite names."""
    path: str
    """Where the spec was read, ``REF:PATH``."""
    document: Resource
    """The copy as ``apply`` creates it."""
    prompts: tuple[str, ...] = ()
    """The ``ask_*_on_launch`` flags the copy enables for its cases' launch fields."""
    nodes: tuple[WorkflowNodeSpec, ...] = ()
    """A workflow's nodes as its spec writes them (before they point at copies)."""

    @property
    def kind(self) -> str:
        return self.document.kind

    @property
    def name(self) -> str:
        return self.document.metadata.name

    @property
    def organization(self) -> str | None:
        return self.document.metadata.organization

    @property
    def key(self) -> _Key:
        """What the copy stands for: its kind, the spec's name and its organization."""
        return (self.kind, self.source, self.organization)

    @property
    def targets(self) -> list[NodeTarget]:
        """The job templates and workflows its nodes run."""
        return [target for node in self.nodes if (target := _target(node, self.organization))]


@dataclass(frozen=True)
class TemporarySet:
    """The copies one run creates (job templates first) and the suites bound to them."""

    marker: Marker
    templates: tuple[TemporaryTemplate, ...] = ()
    bindings: dict[str, TemplateBinding] = field(default_factory=dict)
    """Suite name → the pinned copy its cases launch."""
    notes: tuple[str, ...] = ()
    """Why a spec that looks like a suite's template did not bind to it."""

    def copy_of(self, key: _Key) -> TemporaryTemplate | None:
        """The copy of the template ``key`` names (kind, name, organization), if any."""
        return next((template for template in self.templates if template.key == key), None)

    def bound(self, binding: TemplateBinding) -> TemporaryTemplate | None:
        """The copy ``binding`` launches, if it is one."""
        wanted = (binding.kind, binding.name, (binding.scope or {}).get("organization"))
        return next(
            (
                template
                for template in self.templates
                if (template.kind, template.name, template.organization) == wanted
            ),
            None,
        )

    def approval_nodes(self, template: TemporaryTemplate, *, depth: int = 0) -> list[str]:
        """The paths of a workflow copy's approval nodes, those of the copies it runs too."""
        found: list[str] = []
        for node in template.nodes:
            if node.approval is not None:
                found.append(node.id)
            target = _target(node, template.organization)
            nested = self.copy_of(target.key) if target is not None else None
            if nested is not None and nested.kind == WORKFLOW_TEMPLATE and depth < MAX_NESTING:
                found += [
                    f"{node.id}/{path}" for path in self.approval_nodes(nested, depth=depth + 1)
                ]
        return found


def plan_temporary_set(
    selected: Sequence[tuple[Suite, str, Case]],
    specs: Sequence[tuple[str, Resource]],
    *,
    marker: Marker,
    sha: str,
    default_scope: dict[str, str] | None,
    kinds: Callable[[str], ResourceSpec],
) -> TemporarySet:
    """The copies the ``selected`` cases need, from ``specs`` (``(path, document)``).

    A suite binds to the one spec of its template's kind and name in its
    organization (its own, else ``default_scope``'s; any organization without
    either); a workflow copy also needs a copy of every template its nodes run
    that a spec describes. Two specs matching one suite are refused; a spec of
    the suite's template in another organization is noted.
    """
    by_key: dict[_Key, list[tuple[str, Resource]]] = {}
    for path, doc in specs:
        if doc.kind in TEMPLATE_KINDS:
            doc_key = (doc.kind, doc.metadata.name, doc.metadata.organization)
            by_key.setdefault(doc_key, []).append((path, doc))
    nodes = {
        key: _nodes(*entries[0]) for key, entries in by_key.items() if key[0] == WORKFLOW_TEMPLATE
    }
    fields: dict[_Key, set[str]] = {}
    suite_keys: dict[str, _Key] = {}
    notes: dict[str, str] = {}
    for suite, _, case in selected:
        binding = suite.binding(default_scope)
        organization = (binding.scope or {}).get("organization")
        key = _match(by_key, binding.kind, binding.name, organization)
        if key is None:
            elsewhere = [
                path
                for spec_key, entries in by_key.items()
                if spec_key[:2] == (binding.kind, binding.name)
                for path, _ in entries
            ]
            if elsewhere:
                notes[suite.name] = (
                    f"{suite.name}: the spec of {binding.kind} {q(binding.name)} "
                    f"({', '.join(elsewhere)}) is not in organization {organization}, so the "
                    "suite runs the template AWX holds"
                )
            continue
        suite_keys[suite.name] = key
        launch = {**(suite.defaults or Case()).launch, **case.launch}
        fields.setdefault(key, set()).update(launch)
    wanted = _with_node_templates(by_key, nodes, list(fields))
    names = {key: temporary_name(key[1], sha, marker.run_id) for key in wanted}
    templates = [
        _copy(
            *by_key[key][0],
            name=names[key],
            description=marker.render(),
            sha=sha,
            launch_fields=fields.get(key, set()),
            flags=kinds(key[0]).known_fields,
            nodes=nodes.get(key, []),
            names=names,
        )
        for kind in TEMPLATE_KINDS
        for key in wanted
        if key[0] == kind
    ]
    bindings = {
        suite: TemplateBinding(key[0], names[key], _scope(key[2]), pinned=True)
        for suite, key in suite_keys.items()
    }
    return TemporarySet(
        marker=marker, templates=tuple(templates), bindings=bindings, notes=tuple(notes.values())
    )


def _match(
    by_key: Mapping[_Key, list[tuple[str, Resource]]],
    kind: str,
    name: str,
    organization: str | None,
) -> _Key | None:
    """The one spec of ``kind`` and ``name`` in ``organization`` (any, when ``None``)."""
    found = [
        (key, path)
        for key, entries in by_key.items()
        if key[:2] == (kind, name) and (organization is None or key[2] == organization)
        for path, _ in entries
    ]
    if len(found) > 1:
        paths = ", ".join(path for _, path in found)
        raise ConfigError(
            f"{len(found)} specs match {kind} {q(name)}: {paths}; set the suite's organization "
            "or keep one spec per template",
            category="invalid",
            system="awx.suite",
        )
    return found[0][0] if found else None


def _with_node_templates(
    by_key: Mapping[_Key, list[tuple[str, Resource]]],
    nodes: Mapping[_Key, list[WorkflowNodeSpec]],
    keys: list[_Key],
) -> list[_Key]:
    """``keys`` plus every template with a spec that a workflow among them runs, nested too."""
    wanted = list(keys)
    for key in wanted:  # grows while it is walked
        for node in nodes.get(key, []):
            target = _target(node, key[2])
            if target is None:
                continue
            found = _match(by_key, target.kind, target.name, target.organization)
            if found is not None and found not in wanted:
                wanted.append(found)
    return wanted


def _target(node: WorkflowNodeSpec, organization: str | None) -> NodeTarget | None:
    """The job template or workflow ``node`` runs; its organization defaults to the workflow's."""
    if node.run is None or node.run.kind not in TEMPLATE_KINDS:
        return None
    if "organization" in node.run.model_fields_set:
        organization = node.run.organization
    return NodeTarget(node.id, node.run.kind, node.run.name, organization)


def _copy(
    path: str,
    doc: Resource,
    *,
    name: str,
    description: str,
    sha: str,
    launch_fields: set[str],
    flags: frozenset[str],
    nodes: list[WorkflowNodeSpec],
    names: Mapping[_Key, str],
) -> TemporaryTemplate:
    spec = {k: v for k, v in doc.spec.items() if k not in WEBHOOK_FIELDS}
    spec["description"] = description
    spec["scm_branch"] = sha
    prompts = sorted(
        {
            PROMPT_FLAGS[launch]
            for launch in launch_fields - {"scm_branch"}
            if launch in PROMPT_FLAGS
            and PROMPT_FLAGS[launch] in flags
            and spec.get(PROMPT_FLAGS[launch]) is not True
        }
    )
    spec |= dict.fromkeys(prompts, True)
    if "nodes" in spec:
        organization = doc.metadata.organization
        spec["nodes"] = dump_workflow_nodes(_pointed(node, organization, names) for node in nodes)
    metadata = doc.metadata.model_copy(update={"name": name})
    return TemporaryTemplate(
        source=doc.metadata.name,
        path=path,
        document=doc.model_copy(update={"metadata": metadata, "spec": spec}),
        prompts=tuple(prompts),
        nodes=tuple(nodes),
    )


def _pointed(
    node: WorkflowNodeSpec, organization: str | None, names: Mapping[_Key, str]
) -> WorkflowNodeSpec:
    """``node``, running the copy of its template when the run copies that template."""
    target = _target(node, organization)
    if target is None or target.key not in names:
        return node
    [renamed] = rename_references([node], {(target.kind, target.name): names[target.key]})
    return renamed


def _nodes(path: str, doc: Resource) -> list[WorkflowNodeSpec]:
    try:
        return parse_workflow_nodes(doc.spec.get("nodes", []))
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}", category="invalid", system="awx.suite") from exc


def _scope(organization: str | None) -> dict[str, str] | None:
    return None if organization is None else {"organization": organization}


__all__ = [
    "TAG",
    "TEMPLATE_KINDS",
    "WEBHOOK_FIELDS",
    "Marker",
    "NodeTarget",
    "TemporarySet",
    "TemporaryTemplate",
    "leftover",
    "parse_age",
    "plan_temporary_set",
    "source_name",
    "temporary_name",
]
