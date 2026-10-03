"""Test infrastructure for the awx capability test trees.

The :class:`FakeAap` class is defined inline (rather than in a sibling
``_fake_aap.py``) because pytest's ``--import-mode=importlib`` doesn't
expose ``tests`` as a package, so cross-file imports inside the test
tree don't work. Tests reference ``FakeAap`` via the ``fake_aap``
fixture argument.
"""

from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml

from untaped.settings import get_settings, register_profile_settings
from untaped_awx.settings import AwxSettings


class FakeAap:
    """In-memory mock of the slice of AWX's REST API we test against."""

    def __init__(
        self,
        *,
        base_url: str = "https://aap.example.com",
        api_prefix: str = "/api/v2/",
    ) -> None:
        self.base_url = base_url
        self.api_prefix = api_prefix
        self.store: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
        # Many-to-many memberships keyed by (parent_path, parent_id, sub_path)
        # → set of member ids. Populated by associate/disassociate POSTs to
        # ``/<parent_path>/<id>/<sub_path>/`` (e.g. ``/groups/<id>/hosts/``).
        # Members iterate in association order, like AWX's ordered relations.
        self.memberships: dict[tuple[str, int, str], set[int]] = defaultdict(_OrderedMembers)
        self._next_id = 1
        self.actions_called: list[tuple[str, int, str, dict[str, Any]]] = []
        # One-shot test override consumed by the very next ``_action`` call.
        # After consumption, both fields reset to the defaults so back-to-back
        # launches don't share state. Tests that need persistent overrides
        # set these before each call.
        self.next_action_status: str = "successful"
        self.next_action_stdout: str | None = None
        # One-shot job events seeded for the next launched job.
        self.next_action_events: list[dict[str, Any]] = []
        # One-shot fields of the next launched job's record (``job_explanation``…).
        self.next_action_job_fields: dict[str, Any] = {}
        # One-shot ``job_host_summaries`` records of the next launched job.
        self.next_action_host_summaries: list[dict[str, Any]] = []
        # One-shot count of the next launched job's detail reads that answer
        # ``event_processing_finished: false`` (its events and host summaries
        # stay hidden until then), like a finished job AWX is still saving.
        self.next_action_unsaved_reads = 0
        # ``(collection, id)`` to the detail reads left before its events are saved.
        self.unsaved_reads: dict[tuple[str, int], int] = {}
        # One-shot ``ignored_fields`` added to the next launch response (on top
        # of the fields the template's ``ask_*_on_launch`` flags ignore).
        self.next_action_ignored_fields: dict[str, Any] = {}
        self.ignored_write_fields: set[str] = set()
        # Member ids whose associate POST is refused with 403 (e.g. no
        # permission on that credential); disassociation still works.
        self.forbidden_associate_ids: set[int] = set()
        # Execution ids whose ``cancel/`` POST AWX refuses (405 once finished).
        self.refuse_cancel_ids: set[int] = set()
        # ``GET <template>/<id>/copy/`` answers per (api_path, id); default
        # ``{"can_copy": true}`` like AWX's job template copy check.
        self.copy_checks: dict[tuple[str, int], dict[str, Any]] = {}
        self.mask_secret_write_response = False
        self.enrich_survey_spec_response = False
        # HTTP method → error status for ``<template>/<id>/survey_spec/``.
        self.survey_errors: dict[str, int] = {}
        # HTTP status every action POST (``launch/``, ``update/``…) is refused
        # with, e.g. 401 for a rejected token or 503 for an unavailable AWX.
        self.action_error: int | None = None
        # ``(parent_path, sub_path)`` routes an older controller lacks (404).
        self.missing_sub_paths: set[tuple[str, str]] = set()
        # ``(parent_path, sub_path)`` → HTTP statuses its next reads fail with, one per read.
        self.sub_path_errors: dict[tuple[str, str], list[int]] = {}
        # HTTP status every request answers with once set (an expired token, an outage).
        self.every_request_error: int | None = None
        # HTTP status every DELETE answers with once set (e.g. 403 for a missing role).
        self.delete_error: int | None = None
        # ``(collection, id)`` → HTTP status its ``GET <collection>/<id>/`` reads fail with.
        self.detail_errors: dict[tuple[str, int], int] = {}
        # How the job a workflow node runs ends, by node ``identifier``: a mapping of
        # ``status``, ``events``, ``host_summaries``, ``stdout``, ``job_fields`` and
        # ``unsaved_reads`` (the ``next_action_*`` values), or a list of them used one per
        # run (the last repeats).
        # A launched workflow with nodes runs them at once: jobs end as told, approvals
        # stay pending until ``workflow_approvals/<id>/approve/`` or ``deny/``.
        self.node_outcomes: dict[str, dict[str, Any] | list[dict[str, Any]]] = {}
        self._advancing: set[int] = set()
        # ``(store, id)`` of a held node job → [reads left, final status, its node's id].
        self._held: dict[tuple[str, int], list[Any]] = {}

    def seed(self, api_path: str, **fields: Any) -> dict[str, Any]:
        record_id = fields.pop("id", None) or self._next_id
        self._next_id = max(self._next_id, record_id + 1)
        record = {"id": record_id, **fields}
        self.store[api_path][record_id] = record
        return record

    def get_record(self, api_path: str, id_: int) -> dict[str, Any]:
        return self.store[api_path][id_]

    def list_records(self, api_path: str) -> list[dict[str, Any]]:
        return list(self.store[api_path].values())

    def install(self, mock: respx.Router) -> None:
        self.router = mock
        url_re = re.compile(rf"^{re.escape(self.base_url)}{re.escape(self.api_prefix)}.+")
        mock.route(url__regex=url_re.pattern).mock(side_effect=self._dispatch)

    # C901: in-memory AAP HTTP fixture — dispatches on (method, path-shape)
    # for the GET / POST / PUT / PATCH / DELETE families. CC is intrinsic
    # to mocking the API surface; splitting per-method would scatter the
    # routing table across helpers without simplifying any one of them.
    def _dispatch(self, request: httpx.Request) -> httpx.Response:  # noqa: C901
        path = request.url.path[len(self.api_prefix) :]
        parts = [p for p in path.split("/") if p]
        params = dict(request.url.params)
        method = request.method
        body = self._json_body(request)

        if self.every_request_error is not None:
            return _err(self.every_request_error, "refused")
        if len(parts) == 3 and parts[2] == "survey_spec" and method in self.survey_errors:
            return _err(self.survey_errors[method], f"survey {method} rejected")
        if method == "GET":
            if (
                len(parts) == 3
                and parts[0] in _EXECUTION_SUBPATHS
                and parts[2] not in _EXECUTION_SUBPATHS[parts[0]]
            ):
                return _err(404, f"unsupported execution route: {path}")
            if len(parts) == 1:
                return self._list(parts[0], params)
            if len(parts) == 2 and parts[1].isdigit():
                return self._get(parts[0], int(parts[1]))
            if len(parts) == 3 and parts[1].isdigit() and parts[2] == "launch":
                return self._launch_info(parts[0], int(parts[1]))
            if len(parts) == 3 and parts[1].isdigit() and parts[2] == "survey_spec":
                record = self.store.get(parts[0], {}).get(int(parts[1]))
                if record is None:
                    return _err(404, f"{path} not found")
                # Like AWX's ``display_survey_spec``: password defaults masked.
                survey = copy.deepcopy(record.get("survey_spec") or {})
                _mask_survey_defaults(survey)
                return httpx.Response(200, json=survey)
            if len(parts) == 3 and parts[1].isdigit() and parts[2] == "copy":
                if int(parts[1]) not in self.store.get(parts[0], {}):
                    return _err(404, f"{path} not found")
                return httpx.Response(
                    200, json=self.copy_checks.get((parts[0], int(parts[1])), {"can_copy": True})
                )
            if len(parts) == 3 and parts[1].isdigit() and parts[2] == "stdout":
                return self._stdout(parts[0], int(parts[1]), params)
            if len(parts) == 3 and parts[1].isdigit():
                return self._sub_list(parts[0], int(parts[1]), parts[2], params)
            if len(parts) == 4 and parts[1].isdigit() and parts[3].isdigit():
                return self._sub_get(parts[0], int(parts[1]), parts[2], int(parts[3]))
        elif method == "POST":
            if len(parts) == 1:
                return self._create(parts[0], body)
            if len(parts) == 3 and parts[1].isdigit() and parts[2] == "survey_spec":
                return self._post_survey(parts[0], int(parts[1]), body)
            if len(parts) == 3 and parts[1].isdigit() and parts[2] == "copy":
                return self._copy(parts[0], int(parts[1]), body)
            if (
                len(parts) == 3
                and parts[0] in _EXECUTION_SUBPATHS
                and parts[1].isdigit()
                and parts[2] in {"cancel", "relaunch"}
            ):
                return self._execution_action(parts[0], int(parts[1]), parts[2], body)
            if parts[:1] == ["workflow_job_templates"] and parts[2:] == ["workflow_nodes"]:
                return self._create_node(int(parts[1]), body)
            if parts[:1] == ["workflow_approvals"] and parts[2:] in (["approve"], ["deny"]):
                return self._decide_approval(int(parts[1]), parts[2])
            if len(parts) == 3 and parts[1].isdigit() and parts[0] == "workflow_job_template_nodes":
                if parts[2] == "create_approval_template":
                    return self._create_approval(int(parts[1]), body)
                if parts[2] in _NODE_EDGES:
                    return self._edge_post(int(parts[1]), parts[2], body)
            if len(parts) == 3 and parts[1].isdigit():
                # AWX overloads ``POST /<parent>/<id>/<sub>/`` for two
                # things: launching a job/action (body has no ``id``) and
                # associating/disassociating a member (body has ``id``).
                # Discriminate by body shape.
                if "id" in body and isinstance(body["id"], int):
                    return self._sub_post(parts[0], int(parts[1]), parts[2], body)
                # Nested create on inventory: ``POST /inventories/<id>/hosts/``
                # with a host body (no ``id`` field) creates a host and
                # auto-fills ``inventory: <id>``.
                if parts[0] == "inventories" and parts[2] in {"hosts", "groups"}:
                    return self._nested_create(parts[2], int(parts[1]), body)
                if parts[2] == "schedules":
                    body = {**body, "unified_job_template": int(parts[1])}
                    return self._create("schedules", body)
                return self._action(parts[0], int(parts[1]), parts[2], body)
        elif method == "PATCH":
            if len(parts) == 2 and parts[1].isdigit():
                return self._update(parts[0], int(parts[1]), body)
        elif method == "DELETE":
            if len(parts) == 3 and parts[1].isdigit() and parts[2] == "survey_spec":
                record = self.store.get(parts[0], {}).get(int(parts[1]))
                if record is None:
                    return _err(404, f"{path} not found")
                record["survey_spec"] = {}
                return httpx.Response(200, json={})
            if len(parts) == 2 and parts[1].isdigit():
                return self._delete(parts[0], int(parts[1]))
        return _err(404, f"no fake handler for {method} {path}")

    def _list(self, api_path: str, params: dict[str, str]) -> httpx.Response:
        store_collection = _TOP_PATH_STORE.get(api_path, api_path)
        records = self._apply_filters(list(self.store[store_collection].values()), params)
        records = [self._public(api_path, r) for r in records]
        return _page_response(records, params, f"{self.api_prefix}{api_path}/")

    def _get(self, api_path: str, id_: int) -> httpx.Response:
        if (api_path, id_) in self.detail_errors:
            return _err(self.detail_errors[(api_path, id_)], f"{api_path}/{id_}/ refused")
        if api_path == "workflow_jobs":
            self._tick_held()
        record = self.store.get(_TOP_PATH_STORE.get(api_path, api_path), {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/ not found")
        public = self._public(api_path, record)
        unsaved = self.unsaved_reads.get((api_path, id_))
        if unsaved is not None:
            self.unsaved_reads[(api_path, id_)] = max(0, unsaved - 1)
            public = {**public, "event_processing_finished": unsaved == 0}
        return httpx.Response(200, json=public)

    def _stdout(self, api_path: str, id_: int, params: dict[str, str]) -> httpx.Response:
        """Plain-text stdout endpoint (e.g. ``jobs/<id>/stdout/``): the whole log.

        Like AWX's ``txt``/``txt_download`` formats, ``start_line`` is ignored.
        """
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/stdout/ not found")
        text = str(record.get("stdout", ""))
        return httpx.Response(200, text=text, headers={"content-type": "text/plain"})

    def _create(self, api_path: str, body: dict[str, Any]) -> httpx.Response:
        new_id = self._next_id
        self._next_id += 1
        record = {"id": new_id, **self._write_body(api_path, body)}
        self.store[api_path][new_id] = record
        return httpx.Response(201, json=_public(api_path, record))

    def _update(self, api_path: str, id_: int, body: dict[str, Any]) -> httpx.Response:
        record = self.store.get(_TOP_PATH_STORE.get(api_path, api_path), {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/ not found")
        record.update(self._write_body(api_path, body))
        return httpx.Response(200, json=self._public(api_path, record))

    def _copy(self, api_path: str, id_: int, body: dict[str, Any]) -> httpx.Response:
        """``POST <kind>/<id>/copy/``: a new record with the source's fields and members."""
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/copy/ not found")
        if not self.copy_checks.get((api_path, id_), {"can_copy": True}).get("can_copy"):
            return _err(403, "You do not have permission to perform this action.")
        name = body.get("name") or f"{record.get('name')} copy"
        if name == record.get("name"):
            return _err(400, "a copy cannot have the same name")
        self.actions_called.append((api_path, id_, "copy", body))
        new_id = self._next_id
        self._next_id += 1
        copied = {**copy.deepcopy(record), "id": new_id, "name": name}
        self.store[api_path][new_id] = copied
        for (parent, parent_id, sub), members in list(self.memberships.items()):
            if parent == api_path and parent_id == id_:
                self.memberships[(parent, new_id, sub)] = set(members)
        return httpx.Response(201, json=_public(api_path, copied))

    def _post_survey(self, api_path: str, id_: int, body: dict[str, Any]) -> httpx.Response:
        """``POST <template>/<id>/survey_spec/``: AWX's ``$encrypted$`` rules.

        A password default of ``$encrypted$`` keeps the stored default and
        is refused when none exists; any other placeholder use is refused.
        """
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/survey_spec/ not found")
        old = {
            q.get("variable"): q
            for q in (record.get("survey_spec") or {}).get("spec") or []
            if isinstance(q, dict)
        }
        survey = copy.deepcopy(body)
        for question in survey.get("spec") or []:
            if question.get("default") != "$encrypted$":
                continue
            previous = old.get(question.get("variable"), {})
            if question.get("type") != "password" or "default" not in previous:
                return _err(400, "$encrypted$ is a reserved keyword")
            question["default"] = previous["default"]
        if self.enrich_survey_spec_response:
            survey = _enrich_survey_spec(survey)
        record["survey_spec"] = survey
        return httpx.Response(200)

    def _write_body(self, api_path: str, body: dict[str, Any]) -> dict[str, Any]:
        stored = {
            key: copy.deepcopy(value)
            for key, value in body.items()
            if key not in self.ignored_write_fields
            # AWX's template serializers have no survey_spec field: a record
            # write ignores it; only ``<id>/survey_spec/`` changes the survey.
            and not (key == "survey_spec" and api_path in _SURVEY_PATHS)
        }
        if self.enrich_survey_spec_response and "survey_spec" in stored:
            stored["survey_spec"] = _enrich_survey_spec(stored["survey_spec"])
        if self.mask_secret_write_response and api_path in {
            "job_templates",
            "workflow_job_templates",
        }:
            if "webhook_key" in stored:
                stored["webhook_key"] = "$encrypted$"
            _mask_survey_defaults(stored.get("survey_spec"))
        return stored

    def _delete(self, api_path: str, id_: int) -> httpx.Response:
        # Match real AWX: DELETE on a missing id returns 404 (the
        # silent-pop shortcut hid id-typos behind a 204).
        store_path = _TOP_PATH_STORE.get(api_path, api_path)
        if id_ not in self.store.get(store_path, {}):
            return _err(404, f"{api_path}/{id_}/ not found")
        if self.delete_error is not None:
            return _err(self.delete_error, "delete refused")
        if api_path in _UJT_JOB_STORES and any(
            job.get("unified_job_template") == id_ and job.get("status") in _ACTIVE
            for job in self.store[_UJT_JOB_STORES[api_path]].values()
        ):
            # Like AWX's RelatedJobsPreventDeleteMixin: not while a job of it runs.
            return _err(409, "Resource is being used by running jobs.")
        record = self.store[store_path].pop(id_)
        if store_path == "workflow_job_templates":
            # Like AWX: a workflow's nodes, and their approval templates, go with it.
            for node_id in [node["id"] for node in self._nodes_of(id_)]:
                node = self.store["workflow_nodes"].pop(node_id)
                self.store["workflow_approval_templates"].pop(
                    node.get("unified_job_template"), None
                )
        if store_path == "workflow_nodes":
            # Like AWX: edges into the node go with it, and so does its approval.
            for node in self.store["workflow_nodes"].values():
                for relation in _NODE_EDGES:
                    if id_ in node.get(relation, []):
                        node[relation].remove(id_)
            self.store["workflow_approval_templates"].pop(record.get("unified_job_template"), None)
        return httpx.Response(204)

    def _public(self, api_path: str, record: dict[str, Any]) -> dict[str, Any]:
        if _TOP_PATH_STORE.get(api_path, api_path) == "workflow_nodes":
            return self._render_node(record)
        return _public(api_path, record)

    def _render_node(self, record: dict[str, Any]) -> dict[str, Any]:
        """A node as AWX serializes it: edge lists and its template's summary."""
        rendered: dict[str, Any] = {relation: [] for relation in _NODE_EDGES}
        rendered.update(copy.deepcopy(record))
        ujt = record.get("unified_job_template")
        if "summary_fields" not in record and isinstance(ujt, int):
            for path, job_type in _UJT_STORES.items():
                target = self.store.get(path, {}).get(ujt)
                if target is not None:
                    rendered["summary_fields"] = {
                        "unified_job_template": {
                            "id": ujt,
                            "name": target.get("name"),
                            "unified_job_type": job_type,
                        }
                    }
                    break
        return rendered

    def _create_node(self, workflow_id: int, body: dict[str, Any]) -> httpx.Response:
        """``POST workflow_job_templates/<id>/workflow_nodes/``: identifiers are unique."""
        identifier = body.get("identifier") or f"uuid-{self._next_id}"
        if any(
            node.get("workflow_job_template") == workflow_id
            and node.get("identifier") == identifier
            for node in self.store["workflow_nodes"].values()
        ):
            return _err(400, "identifier: already exists in this workflow")
        new = self.seed(
            "workflow_nodes",
            **{
                "all_parents_must_converge": False,
                "extra_data": {},
                **body,
                "identifier": identifier,
                "workflow_job_template": workflow_id,
            },
        )
        self.actions_called.append(("workflow_job_templates", workflow_id, "node", body))
        return httpx.Response(201, json=self._render_node(new))

    def _create_approval(self, node_id: int, body: dict[str, Any]) -> httpx.Response:
        node = self.store["workflow_nodes"].get(node_id)
        if node is None:
            return _err(404, f"workflow_job_template_nodes/{node_id}/ not found")
        approval = self.seed(
            "workflow_approval_templates",
            **{"description": "", "timeout": 0, **body},
        )
        node["unified_job_template"] = approval["id"]
        return httpx.Response(201, json=approval)

    def _edge_post(self, node_id: int, relation: str, body: dict[str, Any]) -> httpx.Response:
        """Associate/disassociate a child node, refusing what AWX refuses."""
        nodes = self.store["workflow_nodes"]
        node, child = nodes.get(node_id), int(body["id"])
        if node is None or child not in nodes:
            return _err(404, "node not found")
        edges = node.setdefault(relation, [])
        if body.get("disassociate"):
            if child in edges:
                edges.remove(child)
            return httpx.Response(204)
        if any(child in node.get(other, []) for other in _NODE_EDGES if other != relation):
            return _err(400, "Relationship not allowed.")
        if child not in edges:
            edges.append(child)
        if _reaches(nodes, child, node_id):
            edges.remove(child)
            return _err(400, "Cycle detected.")
        return httpx.Response(204)

    def _action(
        self,
        api_path: str,
        id_: int,
        action: str,
        body: dict[str, Any],
    ) -> httpx.Response:
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/{action}/")
        if self.action_error is not None:
            return _err(self.action_error, f"{action} refused")
        self.actions_called.append((api_path, id_, action, body))
        # Consume the one-shot overrides so a subsequent launch sees defaults.
        status = self.next_action_status
        stdout = self.next_action_stdout
        events = self.next_action_events
        job_fields = self.next_action_job_fields
        host_summaries = self.next_action_host_summaries
        unsaved_reads = self.next_action_unsaved_reads
        self.next_action_unsaved_reads = 0
        self.next_action_status = "successful"
        self.next_action_stdout = None
        self.next_action_events = []
        self.next_action_job_fields = {}
        self.next_action_host_summaries = []
        new_id = self._next_id
        self._next_id += 1
        result_kind = {
            "job_templates": "job",
            "workflow_job_templates": "workflow_job",
            "projects": "project_update",
            "inventory_sources": "inventory_update",
        }.get(api_path, "job")
        store_path = f"{result_kind}s"
        name = f"{record.get('name', '')}-{action}"
        result = {
            "id": new_id,
            "type": result_kind,
            "name": name,
            "status": status,
        }
        if action == "launch":
            # Real AWX accepts unprompted fields and reports them as ignored,
            # unless the template has the value already (a no-op): its own,
            # its project's branch when it names none, extra vars it saves.
            ignored = {
                field: value
                for field, value in body.items()
                if field in _LAUNCH_PROMPTS
                and not self._prompts_for(record, field)
                and _template_launch_value(record, field) != value
                and not (field == "extra_vars" and _saved_vars(record, value))
                and not (
                    field == "scm_branch"
                    and record.get("scm_branch") == ""
                    and value
                    == self.store["projects"].get(record.get("project"), {}).get("scm_branch")
                )
                and not (
                    field == "credentials"
                    and set(value) <= set(_template_launch_value(record, field))
                )
            }
            ignored.update(self.next_action_ignored_fields)
            self.next_action_ignored_fields = {}
            if ignored:
                result["ignored_fields"] = ignored
        # Always materialise a record so subsequent ``GET <store_path>/<id>/``
        # round trips (e.g. ``WatchJob`` / ``PollingJobMonitor``) succeed.
        # ``stdout`` is optional — only seeded when the test asks for it.
        seed_fields: dict[str, Any] = {
            "id": new_id,
            "name": name,
            "status": status,
            "unified_job_template": id_,
        }
        if stdout is not None:
            seed_fields["stdout"] = stdout
        if api_path == "job_templates" and action == "launch":
            # A job runs its project's checkout of the launched (else template's) ref.
            project = self.store.get("projects", {}).get(record.get("project"), {})
            scm = {
                "scm_branch": body.get("scm_branch", record.get("scm_branch", "")),
                "scm_revision": project.get("scm_revision", ""),
            }
            seed_fields.update(scm)
            result.update(scm)
        self.seed(store_path, **seed_fields, **job_fields)
        result.update(job_fields)  # AWX answers a launch with the job's record
        if api_path == "workflow_job_templates" and action == "launch" and self._nodes_of(id_):
            self._start_workflow(new_id, id_, scm_branch=body.get("scm_branch"))
            result["status"] = "pending"  # as AWX answers a launch: the run polls for more
        for counter, event in enumerate(events, start=1):
            self.seed(f"{result_kind}_events", job=new_id, counter=counter, **event)
        for summary in host_summaries:
            self.seed("job_host_summaries", job=new_id, **summary)
        if unsaved_reads:
            self.unsaved_reads[(store_path, new_id)] = unsaved_reads
            result["event_processing_finished"] = False
        return httpx.Response(200, json=result)

    def _execution_action(
        self, api_path: str, id_: int, action: str, body: dict[str, Any]
    ) -> httpx.Response:
        """``cancel/`` (202, no body) and ``relaunch/`` (201, the new execution)."""
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/{action}/ not found")
        self.actions_called.append((api_path, id_, action, body))
        if action == "cancel":
            if id_ in self.refuse_cancel_ids or record.get("status") in {
                "successful",
                "failed",
                "error",
                "canceled",
            }:
                return _err(405, 'Method "POST" not allowed.')
            record["status"] = "canceled"
            return httpx.Response(202)
        kind = {"jobs": "job", "workflow_jobs": "workflow_job", "ad_hoc_commands": "ad_hoc_command"}
        if api_path not in kind:
            return _err(405, 'Method "POST" not allowed.')
        new = self.seed(api_path, name=record.get("name"), status="successful")
        return httpx.Response(201, json={**new, "type": kind[api_path], "status": "pending"})

    # ---- workflow runs ----

    def _nodes_of(self, template_id: int) -> list[dict[str, Any]]:
        return [
            node
            for node in self.store["workflow_nodes"].values()
            if node.get("workflow_job_template") == template_id
        ]

    def _start_workflow(
        self,
        job_id: int,
        template_id: int,
        *,
        scm_branch: str | None = None,
        parent_node: int | None = None,
    ) -> None:
        """Give a new workflow job one node per template node (edges mapped), then run it."""
        workflow = self.store["workflow_jobs"][job_id]
        workflow.update(status="running", parent_node=parent_node, launch_scm_branch=scm_branch)
        template_nodes = sorted(self._nodes_of(template_id), key=lambda node: node["id"])
        mapped: dict[int, dict[str, Any]] = {}
        for node in template_nodes:
            summary = self._render_node(node).get("summary_fields", {})
            mapped[node["id"]] = self.seed(
                "workflow_job_nodes",
                workflow_job=job_id,
                identifier=node.get("identifier"),
                unified_job_template=node.get("unified_job_template"),
                all_parents_must_converge=bool(node.get("all_parents_must_converge")),
                job=None,
                do_not_run=False,
                summary_fields={"unified_job_template": summary.get("unified_job_template", {})},
            )
        for node in template_nodes:
            for relation in _NODE_EDGES:
                mapped[node["id"]][relation] = [
                    mapped[child]["id"] for child in node.get(relation, [])
                ]
        self._advance_workflow(job_id)

    def _job_nodes(self, job_id: int) -> list[dict[str, Any]]:
        return [
            node
            for node in self.store["workflow_job_nodes"].values()
            if node["workflow_job"] == job_id
        ]

    def _advance_workflow(self, job_id: int) -> None:
        """Run every node whose parents allow it, until none can run; then settle the workflow."""
        if self.store["workflow_jobs"][job_id]["status"] == "canceled":
            return
        self._advancing.add(job_id)
        nodes = self._job_nodes(job_id)
        progress = True
        while progress:
            progress = False
            for node in nodes:
                waiting = node["job"] is None and not node.get("missing_template")
                if waiting and not node["do_not_run"] and _node_ready(node, nodes):
                    self._run_node(node, job_id)
                    progress = True
        self._advancing.discard(job_id)
        self._settle_workflow(job_id, nodes)

    def _run_node(self, node: dict[str, Any], job_id: int) -> None:
        template = node["summary_fields"]["unified_job_template"]
        kind = template.get("unified_job_type")
        if node.get("unified_job_template") is None:
            node["missing_template"] = True  # its template was deleted: AWX fails it, runs nothing
            return
        if kind == "workflow_approval":
            timed_out = bool(self._node_outcome(node).get("timed_out"))
            execution = self.seed(
                "workflow_approvals",
                name=template.get("name"),
                status="failed" if timed_out else "pending",
                timed_out=timed_out,
            )
        elif kind == "workflow_job":
            execution = self.seed(
                "workflow_jobs",
                name=template.get("name"),
                status="running",
                unified_job_template=node["unified_job_template"],
            )
        else:
            execution = self._run_node_job(node, job_id, kind or "job")
        node["job"] = execution["id"]
        node["summary_fields"]["job"] = {
            "id": execution["id"],
            "name": execution.get("name"),
            "status": execution["status"],
            "type": kind,
            "failed": execution["status"] in _FAILED_STATUSES,
        }
        if kind == "workflow_job":
            launch_branch = self.store["workflow_jobs"][job_id].get("launch_scm_branch")
            self._start_workflow(
                execution["id"],
                node["unified_job_template"],
                scm_branch=launch_branch,
                parent_node=node["id"],
            )

    def _node_outcome(self, node: dict[str, Any]) -> dict[str, Any]:
        outcome = self.node_outcomes.get(node.get("identifier") or "", {})
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        return outcome

    def _run_node_job(self, node: dict[str, Any], job_id: int, kind: str) -> dict[str, Any]:
        """The job (or update) a node runs, ended as ``node_outcomes`` says (successful by default).

        With ``hold: N`` it stays ``hold_status`` (default ``running``) for the next N
        reads of any workflow job, then ends and the workflow runs on.
        """
        outcome = self._node_outcome(node)
        store, parent_field = _NODE_JOB_STORES[kind]
        template = self.store[_UJT_STORE_OF[kind]].get(node["unified_job_template"], {})
        project = self.store.get("projects", {}).get(template.get("project"), {})
        branch = self.store["workflow_jobs"][job_id].get("launch_scm_branch")
        status = outcome.get("status", "successful")
        fields: dict[str, Any] = {
            "name": template.get("name"),
            "unified_job_template": node["unified_job_template"],
            "status": outcome.get("hold_status", "running") if outcome.get("hold") else status,
            **outcome.get("job_fields", {}),
        }
        if kind == "job":
            fields["scm_branch"] = branch or template.get("scm_branch", "")
            fields["scm_revision"] = project.get("scm_revision", "")
        if "stdout" in outcome:
            fields["stdout"] = outcome["stdout"]
        job = self.seed(store, **fields)
        for counter, event in enumerate(outcome.get("events", []), start=1):
            self.seed(f"{kind}_events", **{parent_field: job["id"]}, counter=counter, **event)
        for summary in outcome.get("host_summaries", []):
            self.seed("job_host_summaries", job=job["id"], **summary)
        if outcome.get("unsaved_reads"):
            self.unsaved_reads[(store, job["id"])] = outcome["unsaved_reads"]
        if outcome.get("hold"):
            self._held[(store, job["id"])] = [outcome["hold"], status, node["id"]]
        return job

    def _tick_held(self) -> None:
        """A workflow job was read: every held node job waits one read less, and may end."""
        for (store, job_id), held in list(self._held.items()):
            held[0] -= 1
            if held[0] > 0:
                continue
            del self._held[(store, job_id)]
            self.store[store][job_id]["status"] = held[1]
            node = self.store["workflow_job_nodes"][held[2]]
            node["summary_fields"]["job"]["status"] = held[1]
            self._advance_workflow(node["workflow_job"])

    def _settle_workflow(self, job_id: int, nodes: list[dict[str, Any]]) -> None:
        """A workflow still waiting on a node runs on; else it fails on a failure with no path."""
        workflow = self.store["workflow_jobs"][job_id]
        if workflow["status"] == "canceled":
            return
        if any(node["job"] is not None and _job_status(node) not in _DONE for node in nodes):
            workflow["status"] = "running"
            return
        for node in nodes:
            node["do_not_run"] = node["job"] is None and not node.get("missing_template")
        unhandled = [
            node
            for node in nodes
            if _job_status(node) in _FAILED_STATUSES
            and not node["failure_nodes"]
            and not node["always_nodes"]
        ]
        workflow["status"] = "failed" if unhandled else "successful"
        workflow["failed"] = bool(unhandled)
        missing = [str(node["id"]) for node in unhandled if node.get("missing_template")]
        failed = [str(node["id"]) for node in unhandled if not node.get("missing_template")]
        if missing:
            workflow["job_explanation"] = (
                "Workflow job node(s) missing unified job template and error handling path "
                f"[{', '.join(missing)}]"
            )
        elif failed:
            workflow["job_explanation"] = (
                f"No error handling path for workflow job node(s) [{', '.join(failed)}]"
            )
        parent = workflow.get("parent_node")
        if parent is not None:
            parent_node = self.store["workflow_job_nodes"][parent]
            parent_node["summary_fields"]["job"]["status"] = workflow["status"]
            if parent_node["workflow_job"] not in self._advancing:
                self._advance_workflow(parent_node["workflow_job"])

    def _decide_approval(self, approval_id: int, action: str) -> httpx.Response:
        """``POST workflow_approvals/<id>/approve/`` (or ``deny/``): 204; the workflow runs on."""
        approval = self.store["workflow_approvals"].get(approval_id)
        if approval is None:
            return _err(404, f"workflow_approvals/{approval_id}/ not found")
        if approval["status"] != "pending":
            return _err(400, "This workflow step has already been approved or denied.")
        self.actions_called.append(("workflow_approvals", approval_id, action, {}))
        approval["status"] = "successful" if action == "approve" else "failed"
        for node in list(self.store["workflow_job_nodes"].values()):
            if node["job"] == approval_id and _job_type(node) == "workflow_approval":
                node["summary_fields"]["job"]["status"] = approval["status"]
                self._advance_workflow(node["workflow_job"])
        return httpx.Response(204)

    def _launch_info(self, api_path: str, id_: int) -> httpx.Response:
        """``GET <template>/launch/``: prompt flags default to AWX's ``False``."""
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/launch/ not found")
        info: dict[str, Any] = {
            ask: bool(record.get(ask, False)) for ask in _LAUNCH_PROMPTS.values()
        }
        info["defaults"] = {
            field: _template_launch_value(record, field)
            for field in _LAUNCH_PROMPTS
            if field == "credentials" or field in record
        }
        info["survey_enabled"] = bool(record.get("survey_enabled", False))
        info["variables_needed_to_start"] = list(record.get("variables_needed_to_start", []))
        return httpx.Response(200, json=info)

    @staticmethod
    def _prompts_for(record: dict[str, Any], field: str) -> bool:
        if field == "extra_vars" and record.get("survey_enabled"):
            return True
        return bool(record.get(_LAUNCH_PROMPTS[field], False))

    def _sub_list(
        self,
        parent_path: str,
        parent_id: int,
        sub_path: str,
        params: dict[str, str],
    ) -> httpx.Response:
        # AWX's nested URLs sometimes use a ``sub_path`` that differs from
        # the actual collection name (``GET /groups/<id>/children/`` returns
        # Group records, which live in ``self.store["groups"]``). Resolve
        # the storage collection accordingly.
        if (parent_path, sub_path) in self.missing_sub_paths:
            return _err(404, f"{parent_path}/{parent_id}/{sub_path}/ not found")
        if self.sub_path_errors.get((parent_path, sub_path)):
            return _err(self.sub_path_errors[(parent_path, sub_path)].pop(0), "unavailable")
        store_collection = _SUB_PATH_STORE.get((parent_path, sub_path), sub_path)
        membership_key = (parent_path, parent_id, sub_path)
        if membership_key in self.memberships:
            members = self.memberships[membership_key]
            records = [
                self.store[store_collection][i]
                for i in members
                if i in self.store[store_collection]
            ]
        else:
            # ``parent_path[:-1]`` strips exactly one trailing letter (so
            # ``inventories`` → ``inventorie`` is avoided in favour of
            # ``inventory``); ``rstrip`` would chew through every trailing
            # ``s``. Fall through to AWX's snake_case singular FK column.
            singular = _AWX_FK_SINGULAR.get(parent_path, parent_path[:-1])
            # The ``unified_job_template`` arm supports nested collections
            # under unified-template kinds; skip it for sub-paths whose
            # back-reference is a *different* FK column (see
            # ``_SUB_PATH_SKIPS_UJT_FALLBACK``).
            skip_ujt = (parent_path, sub_path) in _SUB_PATH_SKIPS_UJT_FALLBACK
            records = [
                r
                for r in self.store[store_collection].values()
                if (not skip_ujt and r.get("unified_job_template") == parent_id)
                or r.get(singular) == parent_id
            ]
        if self.unsaved_reads.get((parent_path, parent_id)):
            records = []  # AWX has not saved them yet
        if not records and (parent_path, sub_path) in _EVENT_SUB_PATHS:
            records = self._events_from_stdout(parent_path, parent_id)
        records = self._apply_filters(records, params)
        if store_collection == "workflow_nodes":
            records = [self._render_node(record) for record in records]
        if (parent_path, sub_path) in _EVENT_SUB_PATHS and not params.get("no_truncate"):
            records = [_truncated_stdout(record) for record in records]
        return _page_response(
            records, params, f"{self.api_prefix}{parent_path}/{parent_id}/{sub_path}/"
        )

    def _events_from_stdout(self, parent_path: str, parent_id: int) -> list[dict[str, Any]]:
        """A seeded ``stdout`` as AWX serves it through events: one ``verbose`` event per line.

        AWX builds a job's text log from its events, so a test that seeds only
        the log still sees the same lines when a monitor follows the events.
        """
        record = self.store.get(parent_path, {}).get(parent_id, {})
        parent_field = parent_path[:-1]  # ``jobs`` → ``job``, ``project_updates`` → …
        return [
            {"counter": counter, "event": "verbose", "stdout": line, parent_field: parent_id}
            for counter, line in enumerate(str(record.get("stdout", "")).splitlines(), start=1)
        ]

    def _sub_post(
        self,
        parent_path: str,
        parent_id: int,
        sub_path: str,
        body: dict[str, Any],
    ) -> httpx.Response:
        """Associate or disassociate a member via ``POST /<parent>/<id>/<sub>/``.

        AWX uses the same URL for both: a body of ``{"id": N}`` associates,
        ``{"id": N, "disassociate": true}`` removes. Returns 204 on success.
        """
        member_id = int(body["id"])
        key = (parent_path, parent_id, sub_path)
        if not isinstance(self.memberships[key], _OrderedMembers):
            self.memberships[key] = _OrderedMembers(sorted(self.memberships[key]))
        if body.get("disassociate"):
            self.memberships[key].discard(member_id)
        else:
            if member_id in self.forbidden_associate_ids:
                return _err(403, "You do not have permission to perform this action.")
            if sub_path == "credentials":
                # AWX allows at most one credential per credential type.
                credentials = self.store["credentials"]
                new_type = credentials.get(member_id, {}).get("credential_type")
                clash = [
                    other
                    for other in self.memberships[key]
                    if other != member_id
                    and new_type is not None
                    and credentials.get(other, {}).get("credential_type") == new_type
                ]
                if clash:
                    return _err(400, "Cannot assign multiple credentials of the same type.")
            self.memberships[key].add(member_id)
        return httpx.Response(204)

    def _nested_create(
        self,
        sub_path: str,
        parent_id: int,
        body: dict[str, Any],
    ) -> httpx.Response:
        """Create a child resource scoped to its parent via the nested URL.

        Used by the ``inventory_child`` apply strategy: ``POST
        /inventories/<id>/hosts/`` (or ``/groups/``) creates a host or
        group and auto-injects ``inventory: <parent_id>`` so subsequent
        listings under the parent see it. Body must not already carry an
        ``id``.
        """
        new_id = self._next_id
        self._next_id += 1
        record = {"id": new_id, "inventory": parent_id, **body}
        self.store[sub_path][new_id] = record
        return httpx.Response(201, json=record)

    def _sub_get(
        self,
        parent_path: str,
        parent_id: int,
        sub_path: str,
        sub_id: int,
    ) -> httpx.Response:
        record = self.store.get(sub_path, {}).get(sub_id)
        if record is None:
            return _err(404, f"{sub_path}/{sub_id}/ not found")
        return httpx.Response(200, json=record)

    def _apply_filters(
        self, records: list[dict[str, Any]], params: dict[str, str]
    ) -> list[dict[str, Any]]:
        return [r for r in records if self._matches_all(r, params)]

    def _matches_all(self, record: dict[str, Any], params: dict[str, str]) -> bool:
        return _matches_all(record, params, store=self.store)

    @staticmethod
    def _json_body(request: httpx.Request) -> dict[str, Any]:
        if not request.content:
            return {}
        try:
            return json.loads(request.content)  # type: ignore[no-any-return]
        except ValueError:
            return {}
        except TypeError:
            return {}


class _OrderedMembers(set[int]):
    """A member set that iterates in association order (seeded sets: ascending)."""

    def __init__(self, members: Iterable[int] = ()) -> None:
        self._order = list(dict.fromkeys(members))
        super().__init__(self._order)

    def add(self, member: int) -> None:
        if member not in self:
            self._order.append(member)
        super().add(member)

    def discard(self, member: int) -> None:
        if member in self:
            self._order.remove(member)
        super().discard(member)

    def __iter__(self) -> Iterator[int]:
        return iter(list(self._order))


# Launch payload field → the template flag that makes AWX honour it.
_LAUNCH_PROMPTS: dict[str, str] = {
    "extra_vars": "ask_variables_on_launch",
    "limit": "ask_limit_on_launch",
    "inventory": "ask_inventory_on_launch",
    "credentials": "ask_credential_on_launch",
    "scm_branch": "ask_scm_branch_on_launch",
    "job_tags": "ask_tags_on_launch",
    "skip_tags": "ask_skip_tags_on_launch",
    "verbosity": "ask_verbosity_on_launch",
    "diff_mode": "ask_diff_mode_on_launch",
    "job_type": "ask_job_type_on_launch",
}


def _saved_vars(record: dict[str, Any], value: Any) -> bool:
    """Whether every launch extra var is one the template saves with that value."""
    supplied = json.loads(value) if isinstance(value, str) else value
    saved = yaml.safe_load(record.get("extra_vars") or "") or {}
    return all(name in saved and saved[name] == var for name, var in supplied.items())


def _template_launch_value(record: dict[str, Any], field: str) -> Any:
    """The template's own value for a launch field (credentials as ids)."""
    if field == "credentials":
        summary = record.get("summary_fields") or {}
        return [c["id"] for c in summary.get("credentials") or []]
    return record.get(field)


_NODE_EDGES = ("success_nodes", "failure_nodes", "always_nodes")
_ACTIVE = frozenset({"new", "pending", "waiting", "running"})
# A template's collection → the collection of the jobs it launches.
_UJT_JOB_STORES = {"job_templates": "jobs", "workflow_job_templates": "workflow_jobs"}
_FAILED_STATUSES = frozenset({"failed", "error", "canceled"})
_DONE = frozenset({"successful", *_FAILED_STATUSES})


# Execution kind a node runs → (its store, its events' field naming it).
_NODE_JOB_STORES: dict[str, tuple[str, str]] = {
    "job": ("jobs", "job"),
    "project_update": ("project_updates", "project_update"),
    "inventory_update": ("inventory_updates", "inventory_update"),
}
_UJT_STORE_OF = {
    "job": "job_templates",
    "project_update": "projects",
    "inventory_update": "inventory_sources",
}


def _job_status(node: dict[str, Any]) -> str | None:
    """The status of the execution a workflow job node started (``None``: none yet).

    A node whose template was deleted fails without starting anything.
    """
    if node.get("missing_template"):
        return "failed"
    return node["summary_fields"].get("job", {}).get("status")


def _job_type(node: dict[str, Any]) -> str | None:
    return node["summary_fields"].get("job", {}).get("type")


def _node_ready(node: dict[str, Any], nodes: list[dict[str, Any]]) -> bool:
    """A root runs at once; another once a parent ended the way its edge to the node needs.

    With ``all_parents_must_converge``, every parent must have ended that way.
    """
    parents = [
        (parent, relation)
        for parent in nodes
        for relation in _NODE_EDGES
        if node["id"] in parent.get(relation, [])
    ]
    if not parents:
        return True
    followed = [_edge_followed(_job_status(parent), relation) for parent, relation in parents]
    return all(followed) if node.get("all_parents_must_converge") else any(followed)


def _edge_followed(status: str | None, relation: str) -> bool:
    if status not in _DONE:
        return False
    return relation == "always_nodes" or (relation == "success_nodes") == (status == "successful")


# Store → the ``unified_job_type`` a workflow node reports for it.
_UJT_STORES: dict[str, str] = {
    "job_templates": "job",
    "workflow_job_templates": "workflow_job",
    "projects": "project_update",
    "inventory_sources": "inventory_update",
    "workflow_approval_templates": "workflow_approval",
    "system_job_templates": "system_job",
}


def _reaches(nodes: dict[int, dict[str, Any]], start: int, target: int) -> bool:
    """Whether ``target`` is reachable from ``start`` along node edges."""
    pending, seen = [start], set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        for relation in _NODE_EDGES:
            pending.extend(nodes.get(current, {}).get(relation, []))
    return False


# Strict execution routes mirror Controller URLs; arbitrary subcollections must
# not mask unsupported event/stdout requests made by production monitors.
_EXECUTION_SUBPATHS: dict[str, set[str]] = {
    "jobs": {"job_events", "job_host_summaries", "stdout"},
    "workflow_jobs": {"workflow_nodes"},
    "project_updates": {"events", "stdout"},
    "inventory_updates": {"events", "stdout"},
    "ad_hoc_commands": {"events", "stdout"},
}

_EVENT_SUB_PATHS = {
    ("jobs", "job_events"),
    ("project_updates", "events"),
    ("inventory_updates", "events"),
    ("ad_hoc_commands", "events"),
}


# AWX's snake_case FK column on a child record is the singular form of
# the parent collection — but English plural rules don't all collapse to
# "drop the trailing s" (``inventories → inventory``, not ``inventorie``).
_AWX_FK_SINGULAR: dict[str, str] = {
    "inventories": "inventory",
}

# Inverse of ``_AWX_FK_SINGULAR``: given an FK column on a child record
# (e.g. ``inventory``), return the parent collection name (``inventories``).
_AWX_FK_PLURAL: dict[str, str] = {
    "inventory": "inventories",
}

# AWX nested URLs whose ``sub_path`` differs from the actual collection
# name. ``GET /groups/<id>/children/`` returns Group records (which live
# under ``self.store["groups"]``), not records from a fictional
# ``self.store["children"]``.
_SUB_PATH_STORE: dict[tuple[str, str], str] = {
    ("groups", "children"): "groups",
    ("workflow_jobs", "workflow_nodes"): "workflow_job_nodes",
    ("project_updates", "events"): "project_update_events",
    ("inventory_updates", "events"): "inventory_update_events",
    ("ad_hoc_commands", "events"): "ad_hoc_command_events",
}

# Top-level URLs that are collection-wide views of records seeded under
# another name. ``GET /workflow_job_template_nodes/`` returns the same
# node records that ``GET /workflow_job_templates/<id>/workflow_nodes/``
# serves (seeded under ``self.store["workflow_nodes"]``), matching real
# AWX where both endpoints expose one WorkflowJobTemplateNode table.
_TOP_PATH_STORE: dict[str, str] = {
    "workflow_job_template_nodes": "workflow_nodes",
}

# Nested sub-paths whose back-reference is *not* the polymorphic
# ``unified_job_template`` column. See ``_sub_list`` for the reason —
# without skipping the OR clause, a recursion test where workflow A
# contains a node pointing at workflow B would also see that node when
# listing B's own contents.
_SUB_PATH_SKIPS_UJT_FALLBACK: set[tuple[str, str]] = {
    ("workflow_job_templates", "workflow_nodes"),
    ("workflow_jobs", "workflow_nodes"),
}


# C901: filter-param matcher for ``?key=val&...`` queries on the in-memory
# store. CC scales with the supported predicate shapes (exact, ``__in``,
# nested-FK lookup, sub-endpoint membership). Each branch is one
# AAP-supported filter form — the matcher is the spec.
def _matches_all(  # noqa: C901
    record: dict[str, Any],
    params: dict[str, str],
    *,
    store: dict[str, dict[int, dict[str, Any]]] | None = None,
) -> bool:
    for key, value in params.items():
        if key in {"page", "page_size", "order_by", "no_truncate"}:
            continue
        if key == "search":
            term = value.lower()
            name = str(record.get("name", "")).lower()
            description = str(record.get("description", "")).lower()
            if term not in name and term not in description:
                return False
            continue
        if key.endswith("__name"):
            base = key[: -len("__name")]
            segments = base.split("__")
            # Walk the FK chain through the store so the fake mirrors
            # AWX's Django ORM join semantics. This handles both the
            # multi-segment case (``inventory__organization__name``) and
            # the single-segment case (``inventory__name``) without
            # requiring records to be denormalised — newly-created
            # records via nested endpoints don't carry the ``<x>_name``
            # shorthand that ``_apply_filters`` previously relied on.
            if store is not None:
                related = _walk_join(record, segments, store=store)
                related_name = related.get("name") if related is not None else None
                if str(related_name or "") == value:
                    continue
                # Fall through to the denormalised shorthand in case the
                # caller seeded ``<x>_name`` directly without an FK chain.
            flat = f"{base}_name"
            if str(record.get(flat, "")) != value:
                return False
            continue
        if key.endswith("__isnull"):
            base = key[: -len("__isnull")]
            if (record.get(base) is None) != (value == "true"):
                return False
            continue
        if key.endswith("__contains"):
            base = key[: -len("__contains")]
            if value not in str(record.get(base, "")):
                return False
            continue
        if key.endswith("__icontains"):
            base = key[: -len("__icontains")]
            if value.lower() not in str(record.get(base, "")).lower():
                return False
            continue
        if key.endswith("__in"):
            base = key[: -len("__in")]
            wanted = {v.strip() for v in value.split(",") if v.strip()}
            if str(record.get(base, "")) not in wanted:
                return False
            continue
        if key.endswith("__gt"):
            base = key[: -len("__gt")]
            if not _numeric_compare(record.get(base), value, lambda a, b: a > b):
                return False
            continue
        if key.endswith("__gte"):
            base = key[: -len("__gte")]
            if not _numeric_compare(record.get(base), value, lambda a, b: a >= b):
                return False
            continue
        if key.endswith("__lt"):
            base = key[: -len("__lt")]
            if not _numeric_compare(record.get(base), value, lambda a, b: a < b):
                return False
            continue
        actual = record.get(key, "")
        # AWX reads a boolean filter as ``true``/``false``.
        shown = str(actual).lower() if isinstance(actual, bool) else str(actual)
        if shown != value:
            return False
    return True


def _walk_join(
    record: dict[str, Any],
    path: list[str],
    *,
    store: dict[str, dict[int, dict[str, Any]]],
) -> dict[str, Any] | None:
    """Walk an ORM-style FK chain (e.g. ``inventory__organization``).

    At each segment, look up ``record[<segment>]`` (the numeric FK) in
    ``store[<plural>]`` (with the small ``inventory → inventories``
    irregular plural). Returns ``None`` if any link is missing. Mirrors
    AWX's filter layer joining through related fields so a query like
    ``?inventory__organization__name=Default`` actually matches a host
    whose inventory's organization is ``Default``.
    """
    current = record
    for segment in path:
        fk_id = current.get(segment)
        if not isinstance(fk_id, int):
            return None
        collection = _AWX_FK_PLURAL.get(segment, f"{segment}s")
        related = store.get(collection, {}).get(fk_id)
        if related is None:
            return None
        current = related
    return current


def _numeric_compare(
    field_value: Any,
    raw_param: str,
    op: Callable[[int, int], bool],
) -> bool:
    """Compare numeric ``field_value`` to a string-typed query param.

    AWX returns counters / ids as integers but URL params arrive as
    strings; coerce both to int for the comparison and treat any
    non-coercible value as not matching (mirrors AWX's behaviour for
    type-mismatched filters).
    """
    try:
        return op(int(field_value), int(raw_param))
    except TypeError:
        return False
    except ValueError:
        return False


_SURVEY_PATHS = {"job_templates", "workflow_job_templates"}


def _public(api_path: str, record: dict[str, Any]) -> dict[str, Any]:
    """A record as AWX serializes it: templates never carry ``survey_spec``."""
    if api_path in _SURVEY_PATHS and "survey_spec" in record:
        return {k: v for k, v in record.items() if k != "survey_spec"}
    return record


def _enrich_survey_spec(value: Any) -> Any:
    enriched = copy.deepcopy(value)
    if not isinstance(enriched, dict):
        return enriched
    questions = enriched.get("spec")
    if not isinstance(questions, list):
        return enriched
    for question in questions:
        if not isinstance(question, dict):
            continue
        question.setdefault("required", False)
        question.setdefault("min", None)
        question.setdefault("max", None)
        question.setdefault("new_question", False)
    return enriched


def _mask_survey_defaults(value: Any) -> None:
    if not isinstance(value, dict):
        return
    questions = value.get("spec")
    if not isinstance(questions, list):
        return
    for question in questions:
        if (
            isinstance(question, dict)
            and question.get("type") == "password"
            and question.get("default") not in (None, "")
        ):
            question["default"] = "$encrypted$"


def _page_response(
    records: list[dict[str, Any]], params: dict[str, str], url: str
) -> httpx.Response:
    """One page of ``records`` as AWX lists them: ``order_by`` (comma-separated keys,
    ``-`` for descending), ``page`` and ``page_size`` (default 200), and a ``next`` link."""
    for key in reversed([k for k in params.get("order_by", "").split(",") if k]):
        field = key.lstrip("-")
        records = sorted(
            records, key=lambda record: record.get(field) or 0, reverse=key.startswith("-")
        )
    page, page_size = int(params.get("page", "1")), int(params.get("page_size", "200"))
    start = (page - 1) * page_size
    next_url = None
    if start + page_size < len(records):
        next_url = f"{url}?page={page + 1}&page_size={page_size}"
    return httpx.Response(
        200,
        json={
            "count": len(records),
            "next": next_url,
            "previous": None,
            "results": records[start : start + page_size],
        },
    )


_EVENT_STDOUT_LIMIT = 1024
"""Characters of event ``stdout`` AWX serves without ``no_truncate``."""


def _truncated_stdout(record: dict[str, Any]) -> dict[str, Any]:
    stdout = str(record.get("stdout", ""))
    if len(stdout) <= _EVENT_STDOUT_LIMIT:
        return record
    return {**record, "stdout": stdout[:_EVENT_STDOUT_LIMIT] + "\u2026"}


def _err(status: int, detail: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": detail})


# ---- pytest fixtures ----


@pytest.fixture(autouse=True)
def _register_awx_settings(_isolate_config_registry_for_tests: None) -> None:
    """Register the ``awx`` profile section for the awx test trees.

    Depends on the root reset fixture so registration happens after the
    config-registry reset (production registers via composition;
    capability tests invoking the sub-app directly mirror that here).
    """
    register_profile_settings("awx", AwxSettings)
    get_settings.cache_clear()


@pytest.fixture
def aap_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    # Not in the file: a plaintext token there warns on every run.
    monkeypatch.setenv("UNTAPED_AWX__TOKEN", "secret")
    return cfg


@pytest.fixture
def fake_aap(aap_config: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAap]:
    """Fake Controller responses with immediate polls and HTTP retries.

    Keep the real cancellation check; timing/backoff is tested separately
    with injected sleeps in the polling and HTTP retry unit tests.
    """
    from untaped_awx.cli.context import AwxContext

    real_pause = AwxContext.pause

    def pause(ctx: AwxContext, seconds: float) -> None:
        real_pause(ctx, 0)

    monkeypatch.setattr(AwxContext, "pause", pause)
    monkeypatch.setattr("untaped.http._sleep", lambda _: None)
    fake = FakeAap()
    with respx.mock(base_url=fake.base_url, assert_all_called=False) as mock:
        fake.install(mock)
        yield fake


@pytest.fixture
def seeded_default_org(fake_aap: FakeAap) -> FakeAap:
    """``FakeAap`` pre-seeded with organization ``id=1, name="Default"``.

    Returns the same ``FakeAap`` instance as the ``fake_aap`` fixture
    (function-scoped, cached by pytest), post-seed.

    Use when the test's canonical world has one organization called
    ``Default``; tests can then seed FKs with
    ``organization=1, organization_name="Default"``. Opt out (use bare
    ``fake_aap``) for multi-org tests that need ``id=1`` bound to a
    different name, for no-org error-path tests, and for tests using
    module-level seed helpers (``_seed_basic``, ``_seed_fk_prereqs``,
    ``_seed_inventory_with_hosts``) that already seed the Default org.
    """
    fake_aap.seed("organizations", id=1, name="Default")
    return fake_aap


@pytest.fixture
def seeded_job_template_with_credentials(
    seeded_default_org: FakeAap,
) -> tuple[FakeAap, dict[str, int]]:
    """``fake_aap`` pre-seeded with the org/inventory/credentials/JT shape
    used by launch-action payload tests. Returns ``(fake, ids)`` so the
    test can reference seeded ids without redeclaring them."""
    fake_aap = seeded_default_org
    fake_aap.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    fake_aap.seed(
        "credentials",
        id=30,
        name="ssh",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "credentials",
        id=31,
        name="vault",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "job_templates",
        id=10,
        name="alpha",
        organization=1,
        organization_name="Default",
        **{ask: True for ask in _LAUNCH_PROMPTS.values()},
    )
    return fake_aap, {"inventory": 20, "ssh": 30, "vault": 31}


@pytest.fixture
def awx_config() -> AwxSettings:
    """Standard test config matching the YAML in :func:`aap_config`."""
    return AwxSettings(
        base_url="https://aap.example.com",
        token="secret",  # type: ignore[arg-type]
        api_prefix="/api/v2/",
    )
