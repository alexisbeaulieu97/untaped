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

from untaped.capabilities.awx.settings import AwxSettings
from untaped.settings import get_settings, register_profile_settings


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
        # HTTP status every request answers with once set (an expired token, an outage).
        self.every_request_error: int | None = None

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
        record = self.store[store_path].pop(id_)
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
            # unless the value equals the template's own (a no-op).
            ignored = {
                field: value
                for field, value in body.items()
                if field in _LAUNCH_PROMPTS
                and not self._prompts_for(record, field)
                and _template_launch_value(record, field) != value
                and not (field == "extra_vars" and value in ("{}", {}))
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
        seed_fields: dict[str, Any] = {"id": new_id, "name": name, "status": status}
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


def _template_launch_value(record: dict[str, Any], field: str) -> Any:
    """The template's own value for a launch field (credentials as ids)."""
    if field == "credentials":
        summary = record.get("summary_fields") or {}
        return [c["id"] for c in summary.get("credentials") or []]
    return record.get(field)


_NODE_EDGES = ("success_nodes", "failure_nodes", "always_nodes")

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
              token: secret
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


@pytest.fixture
def fake_aap(aap_config: Path) -> Iterator[FakeAap]:
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
