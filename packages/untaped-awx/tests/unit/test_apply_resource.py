"""Unit tests for ApplyResource against stub Protocols."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from untaped_awx.application import ApplyResource
from untaped_awx.domain import Metadata, Resource, ResourceSpec
from untaped_awx.domain.envelope import IdentityRef
from untaped_awx.errors import BadRequest
from untaped_awx.infrastructure.specs import (
    CREDENTIAL_SPEC,
    JOB_TEMPLATE_SPEC,
    PROJECT_SPEC,
    SCHEDULE_SPEC,
)

# ----- Stubs -----


class _StubCatalog:
    def __init__(self, specs: dict[str, ResourceSpec]) -> None:
        self._specs = specs

    def get(self, kind: str) -> ResourceSpec:
        return self._specs[kind]

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def by_cli_name(self, cli_name: str) -> ResourceSpec:
        raise NotImplementedError


class _StubFk:
    def __init__(self, names: dict[tuple[str, str], int]) -> None:
        self._names = names

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        return self._names[(kind, name)]

    def id_to_name(self, kind: str, id_: int) -> str:
        for (k, n), i in self._names.items():
            if k == kind and i == id_:
                return n
        raise KeyError((kind, id_))

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        return value["kind"], self._names[(value["kind"], value["name"])]


class _StubClient:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self.existing = existing
        self.created: dict[str, Any] | None = None
        self.updated: tuple[int, dict[str, Any]] | None = None
        self.find_calls: list[dict[str, str]] = []

    def list(
        self, spec: ResourceSpec, *, params: Any = None, limit: Any = None
    ) -> Iterator[dict[str, Any]]:
        return iter([])

    def get(self, spec: ResourceSpec, id_: int) -> dict[str, Any]:
        raise NotImplementedError

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> dict[str, Any] | None:
        self.find_calls.append(params)
        return self.existing

    def create(self, spec: ResourceSpec, payload: dict[str, Any]) -> dict[str, Any]:
        self.created = payload
        return {"id": 999, **payload}

    def update(self, spec: ResourceSpec, id_: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.updated = (id_, payload)
        return {"id": id_, **payload}

    def delete(self, spec: ResourceSpec, id_: int) -> None:
        raise NotImplementedError

    def action(
        self,
        spec: ResourceSpec,
        id_: int,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


class _StubStrategy:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self.existing = existing
        self.created: tuple[dict[str, Any], dict[str, Any]] | None = None
        self.updated: tuple[dict[str, Any], dict[str, Any]] | None = None

    def find_existing(self, spec, identity, *, client, fk):  # type: ignore[no-untyped-def]
        return self.existing

    def create(self, spec, payload, identity, *, client, fk):  # type: ignore[no-untyped-def]
        self.created = (payload, identity)
        return {"id": 1, **payload}

    def update(self, spec, existing, payload, *, client, fk):  # type: ignore[no-untyped-def]
        self.updated = (existing, payload)
        return {"id": existing["id"], **payload}


class _StubStrategies:
    def __init__(self, strategy: _StubStrategy) -> None:
        self._strategy = strategy

    def get(self, name: str) -> _StubStrategy:
        return self._strategy


def _make_apply(
    *,
    catalog_specs: dict[str, ResourceSpec],
    fk_names: dict[tuple[str, str], int],
    strategy: _StubStrategy,
    warn: list[str] | None = None,
) -> ApplyResource:
    warn_list = warn if warn is not None else []
    return ApplyResource(
        client=_StubClient(),  # type: ignore[arg-type]
        catalog=_StubCatalog(catalog_specs),  # type: ignore[arg-type]
        fk=_StubFk(fk_names),  # type: ignore[arg-type]
        strategies=_StubStrategies(strategy),  # type: ignore[arg-type]
        warn=warn_list.append,
    )


# ----- Tests -----


def test_preview_does_not_write() -> None:
    strategy = _StubStrategy(existing=None)
    apply = _make_apply(
        catalog_specs={"Project": PROJECT_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"description": "demo", "scm_type": "git"},
    )
    outcome = apply(resource)
    assert outcome.action == "preview"
    assert strategy.created is None
    assert strategy.updated is None


def test_create_when_no_existing() -> None:
    strategy = _StubStrategy(existing=None)
    apply = _make_apply(
        catalog_specs={"Project": PROJECT_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"description": "demo", "scm_type": "git"},
    )
    outcome = apply(resource, write=True)
    assert outcome.action == "created"
    assert strategy.created is not None
    payload, _ = strategy.created
    assert payload["organization"] == 1  # FK resolved
    assert payload["scm_type"] == "git"


def test_update_when_existing_differs() -> None:
    existing = {
        "id": 42,
        "name": "playbooks",
        "organization": 1,
        "description": "old",
        "scm_type": "git",
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"Project": PROJECT_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"description": "new", "scm_type": "git"},
    )
    outcome = apply(resource, write=True)
    assert outcome.action == "updated"
    assert strategy.updated is not None
    _, patch_payload = strategy.updated
    # Only changed fields are PATCHed
    assert patch_payload == {"description": "new"}


def test_unchanged_when_existing_matches() -> None:
    existing = {
        "id": 42,
        "name": "playbooks",
        "organization": 1,
        "description": "demo",
        "scm_type": "git",
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"Project": PROJECT_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"description": "demo", "scm_type": "git"},
    )
    outcome = apply(resource, write=True)
    assert outcome.action == "unchanged"
    assert strategy.updated is None


def test_encrypted_at_declared_path_is_preserved() -> None:
    """JT's `webhook_key` is a declared secret; PATCH must skip it."""
    existing = {
        "id": 42,
        "name": "deploy",
        "organization": 1,
        "playbook": "deploy.yml",
        "webhook_key": "$encrypted$",
        "description": "old",
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"JobTemplate": JOB_TEMPLATE_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={
            "playbook": "deploy.yml",
            "description": "new",
            "webhook_key": "$encrypted$",
        },
    )
    outcome = apply(resource, write=True)
    assert outcome.action == "updated"
    assert "webhook_key" in outcome.preserved_secrets
    assert strategy.updated is not None
    _, patch_payload = strategy.updated
    # webhook_key is NOT in the PATCH (preserved)
    assert "webhook_key" not in patch_payload
    assert patch_payload == {"description": "new"}


def test_nested_encrypted_does_not_mutate_original_payload() -> None:
    """Nested ``$encrypted$`` values must not leak through a shallow copy.

    `survey_spec.spec[*].default` is declared in the JT ``secret_paths``,
    so apply drops those values from the PATCH — but only from the write
    path, never from the user-supplied resource.
    """
    existing = {
        "id": 42,
        "name": "deploy",
        "organization": 1,
        "playbook": "deploy.yml",
        "survey_spec": {
            "spec": [
                {
                    "variable": "pw",
                    "default": "$encrypted$",
                    "question_name": "Password",
                },
            ]
        },
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"JobTemplate": JOB_TEMPLATE_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    spec_data = {
        "playbook": "deploy.yml",
        "survey_spec": {
            "spec": [
                {
                    "variable": "pw",
                    "default": "$encrypted$",
                    "question_name": "Password",
                },
            ]
        },
    }
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec=spec_data,
    )
    apply(resource, write=True)
    # The user-supplied resource.spec must NOT have been mutated.
    survey_after = resource.spec["survey_spec"]["spec"][0]
    assert "default" in survey_after, "shallow copy mutated original payload"
    assert survey_after["default"] == "$encrypted$"


def test_undeclared_encrypted_warns_and_drops() -> None:
    """An $encrypted$ at a path NOT in spec.secret_paths fires a warning."""
    existing = {
        "id": 42,
        "name": "playbooks",
        "organization": 1,
        "scm_type": "git",
    }
    strategy = _StubStrategy(existing=existing)
    warnings: list[str] = []
    apply = _make_apply(
        catalog_specs={"Project": PROJECT_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
        warn=warnings,
    )
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"scm_type": "git", "scm_url": "$encrypted$"},  # not declared
    )
    outcome = apply(resource, write=True)
    assert outcome.dropped_undeclared_secrets == ["scm_url"]
    assert any("undeclared" in w and "scm_url" in w for w in warnings)


def test_create_with_placeholder_secret_errors() -> None:
    """A new credential/JT can't have $encrypted$ at a declared secret path."""
    strategy = _StubStrategy(existing=None)
    apply = _make_apply(
        catalog_specs={"JobTemplate": JOB_TEMPLATE_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={"playbook": "deploy.yml", "webhook_key": "$encrypted$"},
    )
    with pytest.raises(BadRequest):
        apply(resource, write=True)


def test_fks_resolved_for_create() -> None:
    """Project's `credential` FK is name in the file, ID in the payload."""
    strategy = _StubStrategy(existing=None)
    apply = _make_apply(
        catalog_specs={"Project": PROJECT_SPEC},
        fk_names={("Organization", "Default"): 1, ("Credential", "scm-key"): 7},
        strategy=strategy,
    )
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"scm_type": "git", "credential": "scm-key"},
    )
    apply(resource, write=True)
    assert strategy.created is not None
    payload, _ = strategy.created
    assert payload["credential"] == 7
    assert payload["organization"] == 1


def test_apply_rejects_read_only_kind() -> None:
    """Read-only kinds (Credential, Inventory, Organization, CredentialType)
    must not flow through ApplyResource — top-level ``apply <file>`` would
    otherwise issue create/update calls against deferred-CRUD endpoints.
    """
    strategy = _StubStrategy(existing=None)
    apply = _make_apply(
        catalog_specs={"Credential": CREDENTIAL_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="Credential",
        metadata=Metadata(name="scm-key", organization="Default"),
        spec={"credential_type": 1},
    )
    with pytest.raises(BadRequest, match="does not support apply"):
        apply(resource, write=True)
    assert strategy.created is None
    assert strategy.updated is None


def test_apply_unchanged_with_nested_encrypted_preserves_field() -> None:
    """Re-applying a JT whose only ``$encrypted$`` is a nested survey default
    is a no-op — survey_spec is omitted from the PATCH so AWX retains the
    nested secret.
    """
    survey = {
        "spec": [
            {"variable": "pw", "default": "$encrypted$", "question_name": "Password"},
        ]
    }
    existing = {
        "id": 42,
        "name": "deploy",
        "organization": 1,
        "playbook": "deploy.yml",
        "survey_spec": survey,
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"JobTemplate": JOB_TEMPLATE_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={"playbook": "deploy.yml", "survey_spec": survey},
    )
    outcome = apply(resource, write=True)
    assert outcome.action == "unchanged"
    assert strategy.updated is None


def test_apply_sibling_change_alongside_nested_secret_raises() -> None:
    """Renaming a survey question while the question's default is still
    ``$encrypted$`` must raise — PATCHing survey_spec would clobber the
    nested secret.
    """
    existing = {
        "id": 42,
        "name": "deploy",
        "organization": 1,
        "playbook": "deploy.yml",
        "survey_spec": {
            "spec": [
                {
                    "variable": "pw",
                    "default": "$encrypted$",
                    "question_name": "Password",
                },
            ]
        },
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"JobTemplate": JOB_TEMPLATE_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={
            "playbook": "deploy.yml",
            "survey_spec": {
                "spec": [
                    {
                        "variable": "pw",
                        "default": "$encrypted$",
                        "question_name": "New Password Prompt",
                    },
                ]
            },
        },
    )
    with pytest.raises(BadRequest, match="survey_spec"):
        apply(resource, write=True)
    assert strategy.updated is None


def test_apply_real_secret_value_alongside_sibling_change_succeeds() -> None:
    """Inlining the real secret unblocks the sibling rename — the PATCH
    sends the full survey_spec including the new value.
    """
    existing = {
        "id": 42,
        "name": "deploy",
        "organization": 1,
        "playbook": "deploy.yml",
        "survey_spec": {
            "spec": [
                {
                    "variable": "pw",
                    "default": "$encrypted$",
                    "question_name": "Password",
                },
            ]
        },
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"JobTemplate": JOB_TEMPLATE_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    new_survey = {
        "spec": [
            {
                "variable": "pw",
                "default": "actual-secret",
                "question_name": "New Password Prompt",
            },
        ]
    }
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={"playbook": "deploy.yml", "survey_spec": new_survey},
    )
    outcome = apply(resource, write=True)
    assert outcome.action == "updated"
    assert strategy.updated is not None
    _, patch_payload = strategy.updated
    assert patch_payload["survey_spec"] == new_survey


def test_schedule_inventory_fk_uses_parent_organization() -> None:
    """Schedule's ``metadata.parent.organization`` must drive the inventory
    FK scope. With ``metadata.organization=None`` (typical for saved
    schedules), the only org information available is on ``parent``.
    """
    captured_scopes: list[dict[str, str] | None] = []

    class _RecordingFk:
        def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
            captured_scopes.append(scope)
            return 99

        def id_to_name(self, kind: str, id_: int) -> str:
            raise KeyError((kind, id_))

        def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
            return value["kind"], 1

    strategy = _StubStrategy(existing=None)
    apply = ApplyResource(
        client=_StubClient(),  # type: ignore[arg-type]
        catalog=_StubCatalog({"Schedule": SCHEDULE_SPEC}),  # type: ignore[arg-type]
        fk=_RecordingFk(),  # type: ignore[arg-type]
        strategies=_StubStrategies(strategy),  # type: ignore[arg-type]
    )
    resource = Resource(
        kind="Schedule",
        metadata=Metadata(
            name="nightly",
            organization=None,
            parent=IdentityRef(kind="JobTemplate", name="deploy", organization="OrgB"),
        ),
        spec={"rrule": "FREQ=DAILY;COUNT=1", "inventory": "shared-name"},
    )
    apply(resource, write=True)
    # The inventory FK lookup must have received the parent's organization.
    inventory_scopes = [s for s in captured_scopes if s is not None]
    assert inventory_scopes == [{"organization": "OrgB"}]
