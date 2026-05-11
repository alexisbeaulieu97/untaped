"""Unit tests for the ``SaveResource`` use case."""

from __future__ import annotations

from typing import Any, cast

from untaped_awx.application import SaveResource
from untaped_awx.application.ports import FkResolver, ResourceClient
from untaped_awx.domain import ResourceSpec, ServerRecord
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC, PROJECT_SPEC, SCHEDULE_SPEC


class _StubClient:
    """Minimal stub covering only ``find_by_identity``.

    Returns the canned record directly — no need to assert on the
    ``(name, scope) → params`` translation path here (see
    ``test_get_resource.py`` for that). ``SaveResource.__call__`` only
    invokes ``find_by_identity`` followed by an in-memory record-to-
    Resource transform; the ``list`` and ``paginate_sub_endpoint``
    paths only fire from ``save --all`` and from sub-endpoint multi-FKs
    (Group.hosts / Group.children), neither of which these tests cover.
    """

    def __init__(self, *, find_result: dict[str, Any]) -> None:
        self._find_result = find_result

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        return ServerRecord(**self._find_result)


class _StubFk:
    """Minimal FK resolver — SaveResource only calls ``id_to_name``."""

    def __init__(self, names: dict[tuple[str, int], str]) -> None:
        self._by_id = names

    def id_to_name(self, kind: str, id_: int) -> str:
        return self._by_id[(kind, id_)]


def test_save_resource_translates_fk_ids_to_names() -> None:
    client = _StubClient(
        find_result={
            "id": 99,
            "name": "deploy",
            "organization": 1,
            "project": 5,
            "inventory": 7,
            "playbook": "deploy.yml",
            "credentials": [10, 11],
        }
    )
    fk = _StubFk(
        {
            ("Organization", 1): "Default",
            ("Project", 5): "playbooks",
            ("Inventory", 7): "prod",
            ("Credential", 10): "ssh-key",
            ("Credential", 11): "vault-pw",
        }
    )
    use = SaveResource(cast(ResourceClient, client), cast(FkResolver, fk))
    saved = use(JOB_TEMPLATE_SPEC, name="deploy", scope={"organization": "Default"})

    assert saved.kind == "JobTemplate"
    assert saved.metadata.name == "deploy"
    assert saved.metadata.organization == "Default"
    assert saved.spec["project"] == "playbooks"
    assert saved.spec["inventory"] == "prod"
    assert saved.spec["credentials"] == ["ssh-key", "vault-pw"]
    assert saved.spec["playbook"] == "deploy.yml"


def test_save_resource_strips_read_only_fields() -> None:
    client = _StubClient(
        find_result={
            "id": 1,
            "name": "playbooks",
            "organization": 1,
            "scm_type": "git",
            "summary_fields": {"organization": {"name": "Default"}},
            "last_job_run": "2025-01-01",  # read-only
        }
    )
    fk = _StubFk({("Organization", 1): "Default"})
    use = SaveResource(cast(ResourceClient, client), cast(FkResolver, fk))
    saved = use(PROJECT_SPEC, name="playbooks", scope={"organization": "Default"})
    assert "last_job_run" not in saved.spec
    assert "summary_fields" not in saved.spec
    assert "id" not in saved.spec


def test_save_schedule_extracts_polymorphic_parent() -> None:
    client = _StubClient(
        find_result={
            "id": 5,
            "name": "nightly",
            "rrule": "FREQ=DAILY",
            "enabled": True,
            "summary_fields": {
                "unified_job_template": {
                    "name": "deploy",
                    "unified_job_type": "job_template",
                    "organization_name": "Default",
                }
            },
        }
    )
    fk = _StubFk({})
    use = SaveResource(cast(ResourceClient, client), cast(FkResolver, fk))
    saved = use(SCHEDULE_SPEC, name="nightly")
    assert saved.metadata.parent is not None
    assert saved.metadata.parent.kind == "JobTemplate"
    assert saved.metadata.parent.name == "deploy"
    assert saved.metadata.parent.organization == "Default"
