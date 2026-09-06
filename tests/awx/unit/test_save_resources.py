"""Unit tests for the bulk ``SaveResources`` use case."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from untaped.capabilities.awx.application.ports import Catalog, FkResolver, ResourceClient
from untaped.capabilities.awx.application.save_resources import SaveResources
from untaped.capabilities.awx.domain import ResourceSpec, ServerRecord
from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure.specs import (
    CREDENTIAL_SPEC,
    HOST_SPEC,
    JOB_TEMPLATE_SPEC,
    SCHEDULE_SPEC,
)


class _StubClient:
    def __init__(self, records: dict[str, list[dict[str, Any]]]) -> None:
        self._records = records
        self.list_calls: list[tuple[str, dict[str, str] | None]] = []

    def list(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.list_calls.append((spec.kind, params))
        yield from self._records.get(spec.kind, ())

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        raise AssertionError("SaveResources must use bulk list(), not find_by_identity()")

    def paginate_sub_endpoint(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return iter(())


class _StubFk:
    def __init__(self, names: dict[tuple[str, int], str]) -> None:
        self._names = names

    def id_to_name(self, kind: str, id_: int) -> str:
        return self._names[(kind, id_)]


class _StubCatalog:
    def __init__(
        self,
        specs: list[ResourceSpec],
        *,
        cli_names: dict[str, str] | None = None,
    ) -> None:
        self._by_kind = {spec.kind: spec for spec in specs}
        self._cli_names = cli_names or {}

    def get(self, kind: str) -> ResourceSpec:
        try:
            return self._by_kind[kind]
        except KeyError as exc:
            raise AwxApiError(f"unknown kind {kind!r}") from exc

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._by_kind)

    def by_cli_name(self, cli_name: str) -> ResourceSpec:
        try:
            return self.get(self._cli_names[cli_name])
        except KeyError as exc:
            raise AwxApiError(f"unknown CLI name {cli_name!r}") from exc


def _use(
    *,
    records: dict[str, list[dict[str, Any]]],
    specs: list[ResourceSpec],
    names: dict[tuple[str, int], str] | None = None,
    cli_names: dict[str, str] | None = None,
) -> tuple[SaveResources, _StubClient]:
    client = _StubClient(records)
    fk = _StubFk(names or {})
    catalog = _StubCatalog(specs, cli_names=cli_names)
    use = SaveResources(
        cast(ResourceClient, client),
        cast(FkResolver, fk),
        cast(Catalog, catalog),
    )
    return use, client


def test_save_resources_resolves_cli_name_and_generates_safe_full_identity_filename() -> None:
    use, client = _use(
        records={
            "JobTemplate": [
                {
                    "id": 30,
                    "name": "deploy/../app",
                    "organization": 1,
                    "playbook": "deploy.yml",
                }
            ]
        },
        specs=[JOB_TEMPLATE_SPEC],
        names={("Organization", 1): "Default"},
        cli_names={"job-templates": "JobTemplate"},
    )

    outcomes = list(use(kind="job-templates"))

    assert client.list_calls == [("JobTemplate", None)]
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.action == "saved"
    assert outcome.kind == "JobTemplate"
    assert outcome.name == "deploy/../app"
    assert outcome.filename == "JobTemplate__Default__deploy_.._app.yml"
    assert outcome.resource is not None
    assert outcome.resource.metadata.organization == "Default"


def test_save_resources_resolves_domain_kind() -> None:
    use, client = _use(
        records={
            "JobTemplate": [
                {"id": 30, "name": "deploy", "organization": 1, "playbook": "deploy.yml"}
            ]
        },
        specs=[JOB_TEMPLATE_SPEC],
        names={("Organization", 1): "Default"},
    )

    outcomes = list(use(kind="JobTemplate"))

    assert client.list_calls == [("JobTemplate", None)]
    assert [outcome.filename for outcome in outcomes] == ["JobTemplate__Default__deploy.yml"]


def test_save_resources_skips_read_only_kinds_and_preserves_order() -> None:
    use, client = _use(
        records={
            "JobTemplate": [
                {"id": 30, "name": "deploy", "organization": 1, "playbook": "deploy.yml"}
            ],
        },
        specs=[CREDENTIAL_SPEC, JOB_TEMPLATE_SPEC],
        names={("Organization", 1): "Default"},
    )

    outcomes = list(use(all_kinds=True))

    assert client.list_calls == [("JobTemplate", None)]
    assert [(outcome.kind, outcome.action, outcome.detail) for outcome in outcomes] == [
        ("Credential", "skipped", "not roundtrippable in v0"),
        ("JobTemplate", "saved", None),
    ]


def test_save_resources_skips_kinds_with_incompatible_filters() -> None:
    use, client = _use(
        records={
            "JobTemplate": [
                {"id": 30, "name": "deploy", "organization": 1, "playbook": "deploy.yml"}
            ],
            "Schedule": [{"id": 50, "name": "nightly", "rrule": "FREQ=DAILY"}],
        },
        specs=[SCHEDULE_SPEC, JOB_TEMPLATE_SPEC],
        names={("Organization", 1): "Default"},
    )

    outcomes = list(use(all_kinds=True, filters={"organization__name": "Default"}))

    assert client.list_calls == [("JobTemplate", {"organization__name": "Default"})]
    assert [(outcome.kind, outcome.action, outcome.detail) for outcome in outcomes] == [
        ("Schedule", "skipped", "filter field 'organization' not on this kind"),
        ("JobTemplate", "saved", None),
    ]


def test_save_resources_org_scope_uses_kind_specific_filters() -> None:
    use, client = _use(
        records={
            "JobTemplate": [
                {"id": 30, "name": "deploy", "organization": 1, "playbook": "deploy.yml"}
            ],
            "Host": [
                {
                    "id": 101,
                    "name": "web-01",
                    "inventory": 20,
                    "enabled": True,
                    "summary_fields": {
                        "inventory": {
                            "name": "prod",
                            "organization_name": "Default",
                        }
                    },
                }
            ],
            "Schedule": [
                {
                    "id": 50,
                    "name": "nightly",
                    "rrule": "FREQ=DAILY",
                    "summary_fields": {
                        "unified_job_template": {
                            "name": "deploy",
                            "unified_job_type": "job_template",
                            "organization_name": "Default",
                        }
                    },
                }
            ],
        },
        specs=[JOB_TEMPLATE_SPEC, HOST_SPEC, SCHEDULE_SPEC],
        names={("Organization", 1): "Default"},
    )

    outcomes = list(use(all_kinds=True, organization="Default"))

    assert client.list_calls == [
        ("JobTemplate", {"organization__name": "Default"}),
        ("Host", {"inventory__organization__name": "Default"}),
        ("Schedule", None),
    ]
    assert [(outcome.kind, outcome.action) for outcome in outcomes] == [
        ("JobTemplate", "saved"),
        ("Host", "saved"),
        ("Schedule", "saved"),
    ]


def test_save_resources_org_scope_filters_schedules_by_parent_metadata() -> None:
    use, _client = _use(
        records={
            "Schedule": [
                {
                    "id": 50,
                    "name": "default-nightly",
                    "rrule": "FREQ=DAILY",
                    "summary_fields": {
                        "unified_job_template": {
                            "name": "deploy-default",
                            "unified_job_type": "job_template",
                            "organization_name": "Default",
                        }
                    },
                },
                {
                    "id": 51,
                    "name": "other-nightly",
                    "rrule": "FREQ=DAILY",
                    "summary_fields": {
                        "unified_job_template": {
                            "name": "deploy-other",
                            "unified_job_type": "job_template",
                            "organization_name": "Other",
                        }
                    },
                },
            ],
        },
        specs=[SCHEDULE_SPEC],
    )

    outcomes = list(use(kind="Schedule", organization="Default"))

    assert [outcome.name for outcome in outcomes] == ["default-nightly"]


@pytest.mark.parametrize(
    "filters",
    [
        {"modified__gte": "2024-01-01"},
        {"last_job_status": "successful"},
    ],
)
def test_save_resources_accepts_practical_list_filters(filters: dict[str, str]) -> None:
    use, client = _use(
        records={
            "JobTemplate": [
                {"id": 30, "name": "deploy", "organization": 1, "playbook": "deploy.yml"}
            ],
        },
        specs=[JOB_TEMPLATE_SPEC],
        names={("Organization", 1): "Default"},
    )

    outcomes = list(use(kind="JobTemplate", filters=filters))

    assert client.list_calls == [("JobTemplate", filters)]
    assert [(outcome.kind, outcome.action) for outcome in outcomes] == [("JobTemplate", "saved")]


def test_save_resources_filename_includes_parent_identity() -> None:
    use, _client = _use(
        records={
            "Schedule": [
                {
                    "id": 50,
                    "name": "nightly",
                    "rrule": "FREQ=DAILY",
                    "summary_fields": {
                        "unified_job_template": {
                            "name": "deploy",
                            "unified_job_type": "job_template",
                            "organization_name": "Default",
                        }
                    },
                }
            ],
        },
        specs=[SCHEDULE_SPEC],
    )

    [outcome] = list(use(kind="Schedule"))

    assert outcome.filename == "Schedule__JobTemplate__Default__deploy__nightly.yml"
