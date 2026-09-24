"""Unit tests for the batch mutation engine against stub Protocols."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from awx.unit.support import SingleApply
from untaped.capabilities.awx.application.ports import (
    Catalog,
    FkResolver,
    RawHttpResourceClient,
    StrategyResolver,
)
from untaped.capabilities.awx.domain import Metadata, Resource, ResourceSpec
from untaped.capabilities.awx.domain.envelope import IdentityRef
from untaped.capabilities.awx.domain.outcomes import DeleteReceipt
from untaped.capabilities.awx.errors import AwxApiError, BadRequestError
from untaped.capabilities.awx.infrastructure.specs import (
    CREDENTIAL_SPEC,
    GROUP_SPEC,
    JOB_TEMPLATE_SPEC,
    PROJECT_SPEC,
    SCHEDULE_SPEC,
)
from untaped.capabilities.awx.infrastructure.strategies import DefaultApplyStrategy

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

    def get(self, spec: ResourceSpec, id_: int) -> Any:
        if self.existing is None:
            raise BadRequestError("missing record")
        return _ServerRecord(self.existing)

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> dict[str, Any] | None:
        self.find_calls.append(params)
        return self.existing

    def create(self, spec: ResourceSpec, payload: dict[str, Any]) -> dict[str, Any]:
        self.created = payload
        return {"id": 999, **payload}

    def update(self, spec: ResourceSpec, id_: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.updated = (id_, payload)
        return {"id": id_, **payload}

    def delete(self, spec: ResourceSpec, id_: int) -> DeleteReceipt:
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


class _ServerRecord:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, Any]:
        return dict(self._data)


class _FallbackClient(_StubClient):
    def __init__(self, fetched: dict[str, Any]) -> None:
        super().__init__()
        self.fetched = fetched
        self.get_calls: list[tuple[str, int]] = []

    def get(self, spec: ResourceSpec, id_: int) -> _ServerRecord:
        self.get_calls.append((spec.kind, id_))
        return _ServerRecord(self.existing if self.existing is not None else self.fetched)


class _ErrorFallbackClient(_StubClient):
    def __init__(self, error: AwxApiError) -> None:
        super().__init__()
        self.error = error
        self.get_calls: list[tuple[str, int]] = []

    def get(self, spec: ResourceSpec, id_: int) -> _ServerRecord:
        self.get_calls.append((spec.kind, id_))
        raise self.error


class _StubStrategy(DefaultApplyStrategy):
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self.existing = existing
        self.created: tuple[dict[str, Any], dict[str, Any]] | None = None
        self.updated: tuple[dict[str, Any], dict[str, Any]] | None = None

    def find_existing(self, spec, identity, *, client, fk):  # type: ignore[no-untyped-def]
        client.existing = self.existing
        return self.existing

    def create(self, spec, payload, identity, *, client, fk):  # type: ignore[no-untyped-def]
        self.created = (payload, identity)
        return {"id": 1, **payload}

    def update(self, spec, existing, payload, *, client, fk):  # type: ignore[no-untyped-def]
        self.updated = (existing, payload)
        return {"id": existing["id"], **payload}


class _WriteResponseStrategy(_StubStrategy):
    def __init__(
        self,
        *,
        existing: dict[str, Any] | None,
        response: dict[str, Any],
    ) -> None:
        super().__init__(existing=existing)
        self.response = response

    def create(self, spec, payload, identity, *, client, fk):  # type: ignore[no-untyped-def]
        self.created = (payload, identity)
        return dict(self.response)

    def update(self, spec, existing, payload, *, client, fk):  # type: ignore[no-untyped-def]
        self.updated = (existing, payload)
        client.existing = None
        return dict(self.response)


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
    client: RawHttpResourceClient | None = None,
    warn: list[str] | None = None,
    allow_unverified: bool = False,
) -> SingleApply:
    warn_list = warn if warn is not None else []
    kwargs: dict[str, Any] = {}
    if allow_unverified:
        kwargs["allow_unverified"] = True
    return SingleApply(
        client=client
        if client is not None
        else cast(RawHttpResourceClient, _StubClient(existing=strategy.existing)),
        catalog=cast(Catalog, _StubCatalog(catalog_specs)),
        fk=cast(FkResolver, _StubFk(fk_names)),
        strategies=cast(StrategyResolver, _StubStrategies(strategy)),
        warn=warn_list.append,
        **kwargs,
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
    assert outcome.action == "planned"
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


_OLD_PROJECT = {"id": 42, "name": "playbooks", "organization": 1, "description": "old"}
_NEW_PROJECT = {
    "id": 99,
    "name": "playbooks",
    "organization": 1,
    "description": "new",
    "scm_type": "git",
}
_GET_FAILED = AwxApiError("GET failed")


@pytest.mark.parametrize(
    ("existing", "response", "fetched", "allow_unverified", "action", "detail", "gets"),
    [
        # create: a write response that omits a field is verified by one GET
        (None, {"id": 99, "scm_type": "git"}, _NEW_PROJECT, False, "created", None, [99]),
        (None, {"id": 99, "description": "old"}, {**_NEW_PROJECT, "description": "old"},
         False, "partial", "description", [99]),
        (None, {"id": 99, "description": "old"}, {**_NEW_PROJECT, "description": "old"},
         True, "created", "description", [99]),
        (None, {"scm_type": "git"}, _NEW_PROJECT, False, "partial", "no integer", []),
        (None, {"id": 99}, _GET_FAILED, False, "partial", "fallback GET failed", [99]),
        (None, {"id": 99}, _GET_FAILED, True, "created", "fallback GET failed", [99]),
        # update: the existing record is read once more to verify
        ({**_OLD_PROJECT, "scm_type": "git"}, {"id": 42}, {**_OLD_PROJECT, "description": "new"},
         False, "updated", None, [42, 42]),
        ({**_OLD_PROJECT, "scm_type": "git"}, _OLD_PROJECT, _OLD_PROJECT,
         False, "partial", "description", [42, 42]),
        ({**_OLD_PROJECT, "scm_type": "git"}, _OLD_PROJECT, _OLD_PROJECT,
         True, "updated", "description", [42, 42]),
    ],
)  # fmt: skip
def test_write_is_verified_against_the_server_record(
    existing: dict[str, Any] | None,
    response: dict[str, Any],
    fetched: dict[str, Any] | AwxApiError,
    allow_unverified: bool,
    action: str,
    detail: str | None,
    gets: list[int],
) -> None:
    """Unreflected writes fail the row unless ``--allow-unverified`` downgrades them.

    Details and warnings name the field but never the written value.
    """
    client: _FallbackClient | _ErrorFallbackClient = (
        _ErrorFallbackClient(fetched)
        if isinstance(fetched, AwxApiError)
        else _FallbackClient(fetched)
    )
    warnings: list[str] = []
    apply = _make_apply(
        catalog_specs={"Project": PROJECT_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=_WriteResponseStrategy(
            existing=existing, response={"name": "playbooks", "organization": 1, **response}
        ),
        client=cast(RawHttpResourceClient, client),
        warn=warnings,
        allow_unverified=allow_unverified,
    )
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"description": "new", "scm_type": "git"},
    )

    outcome = apply(resource, write=True)

    assert (outcome.action, outcome.unverified) == (action, detail is not None)
    assert client.get_calls == [("Project", id_) for id_ in gets]
    if detail is None:
        assert outcome.detail is None
    else:
        assert outcome.detail is not None
        assert detail in outcome.detail
        assert "new" not in outcome.detail
    if allow_unverified:
        assert any(detail in w and "new" not in w for w in warnings)


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
    with pytest.raises(BadRequestError):
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
    must not flow through the batch mutation engine — top-level ``apply <file>`` would
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
    with pytest.raises(BadRequestError, match="does not support apply"):
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
    with pytest.raises(BadRequestError, match="survey_spec"):
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
    apply = SingleApply(
        client=cast(RawHttpResourceClient, _StubClient()),
        catalog=cast(Catalog, _StubCatalog({"Schedule": SCHEDULE_SPEC})),
        fk=cast(FkResolver, _RecordingFk()),
        strategies=cast(StrategyResolver, _StubStrategies(strategy)),
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


def test_create_raises_when_response_lacks_id_with_membership() -> None:
    """If a strategy ever returns a body without ``id``, membership writes
    can't target it — fail loudly instead of silently dropping members."""

    class _IdLessStrategy(DefaultApplyStrategy):
        def find_existing(self, spec, identity, *, client, fk):  # type: ignore[no-untyped-def]
            return None

        def create(self, spec, payload, identity, *, client, fk):  # type: ignore[no-untyped-def]
            # Deliberately drop ``id`` to simulate a future strategy regression.
            return {"name": payload.get("name")}

        def update(self, spec, existing, payload, *, client, fk):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    apply = SingleApply(
        client=cast(RawHttpResourceClient, _StubClient()),
        catalog=cast(Catalog, _StubCatalog({"Group": GROUP_SPEC})),
        fk=cast(FkResolver, _StubFk({("Host", "web-01"): 101})),
        strategies=cast(StrategyResolver, _StubStrategies(_IdLessStrategy())),
    )
    resource = Resource(
        kind="Group",
        metadata=Metadata(
            name="web-servers",
            parent=IdentityRef(kind="Inventory", name="prod", organization="Default"),
        ),
        spec={"description": "web tier", "hosts": ["web-01"]},
    )
    outcome = apply(resource, write=True)
    assert outcome.action == "partial"
    assert "no integer" in str(outcome.detail)


# ── apply_to_existing: update-only seam for the `patch` mass-patch path ──


def test_apply_to_existing_updates_passed_record_and_never_creates() -> None:
    """``apply_to_existing`` patches the record it's handed and never creates.

    The strategy's ``find_existing`` returns ``None`` (so ``__call__`` would
    *create*), but ``apply_to_existing`` uses the record passed in and only ever
    updates — the contract the ``--stdin`` selection path relies on (you patch
    items you listed, you never declare new ones).
    """
    existing = {
        "id": 42,
        "name": "deploy",
        "organization": 1,
        "description": "old",
        "playbook": "deploy.yml",
    }
    strategy = _StubStrategy(existing=None)
    apply = _make_apply(
        catalog_specs={"JobTemplate": JOB_TEMPLATE_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
        client=cast(RawHttpResourceClient, _StubClient(existing=existing)),
    )
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy"),
        spec={"description": "new"},
    )
    outcome = apply.apply_to_existing(resource, existing, write=True)
    assert outcome.action == "updated"
    assert strategy.created is None  # never created, despite find_existing→None
    assert strategy.updated is not None
    _, patch_payload = strategy.updated
    assert patch_payload == {"description": "new"}  # sparse PATCH, changed field only


def test_apply_to_existing_preview_does_not_write() -> None:
    existing = {
        "id": 42,
        "name": "deploy",
        "organization": 1,
        "description": "old",
        "playbook": "deploy.yml",
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"JobTemplate": JOB_TEMPLATE_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy"),
        spec={"description": "new"},
    )
    outcome = apply.apply_to_existing(resource, existing)  # write defaults False
    assert outcome.action == "planned"
    assert strategy.updated is None


def test_apply_to_existing_rejects_read_only_kind() -> None:
    strategy = _StubStrategy(existing={"id": 1, "name": "scm-key", "organization": 1})
    apply = _make_apply(
        catalog_specs={"Credential": CREDENTIAL_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    resource = Resource(
        kind="Credential",
        metadata=Metadata(name="scm-key"),
        spec={"description": "x"},
    )
    with pytest.raises(BadRequestError, match="does not support apply"):
        apply.apply_to_existing(resource, {"id": 1, "name": "scm-key"}, write=True)
    assert strategy.updated is None


def test_apply_to_existing_resolves_default_environment_fk() -> None:
    """Project's ``default_environment`` FK: name in the overlay, resolved id in
    the sparse PATCH.

    Regression — the field was missing from the Project spec, so
    ``plan_payload`` silently dropped it (and the ``--stdin`` guard rejected it).
    Mirrors :func:`test_fks_resolved_for_create` for the ``credential`` FK.
    """
    existing = {
        "id": 42,
        "name": "playbooks",
        "organization": 1,
        "default_environment": 5,
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"Project": PROJECT_SPEC},
        fk_names={
            ("Organization", "Default"): 1,
            ("ExecutionEnvironment", "prod-ee"): 9,
        },
        strategy=strategy,
    )
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"default_environment": "prod-ee"},
    )
    outcome = apply.apply_to_existing(resource, existing, write=True)
    assert outcome.action == "updated"
    assert strategy.updated is not None
    _, patch_payload = strategy.updated
    assert patch_payload == {"default_environment": 9}  # name→id, sparse PATCH


def test_apply_warns_on_unrecognized_field_but_passes_it_through() -> None:
    """File-mode apply (``__call__``) sends an unknown field through to AWX
    (passthrough, version-robust) but warns once so it stays discoverable."""
    existing = {"id": 42, "name": "playbooks", "organization": 1, "scm_type": "git"}
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
        spec={"scm_type": "git", "future_field": "x"},
    )
    apply(resource, write=True)
    assert any("future_field" in w for w in warnings)
    assert strategy.updated is not None
    _, patch_payload = strategy.updated
    assert patch_payload == {"future_field": "x"}  # passed through, sparse


def test_batch_prepare_warns_once_per_unrecognized_field_set() -> None:
    """Every CLI path (apply/patch/edit) prepares through the engine, so the
    warning lives there, deduplicated so an N-item mass-patch warns once."""
    existing = {"id": 42, "name": "playbooks", "organization": 1, "scm_type": "git"}
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
        spec={"future_field": "x"},
    )
    other_existing = {**existing, "id": 43, "name": "roles"}
    other = resource.model_copy(update={"metadata": Metadata(name="roles", organization="Default")})
    apply.prepare([resource, other], mode="patch", existing=[existing, other_existing])
    assert len(warnings) == 1
    assert "future_field" in warnings[0]


def test_apply_does_not_warn_on_known_or_handled_fields() -> None:
    """Canonical, identity, FK and read-only fields are recognized — no warning
    (a round-tripped ``spec.organization`` / read-only ``id`` must not nag)."""
    existing = {"id": 42, "name": "playbooks", "organization": 1, "scm_type": "git"}
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
        spec={"scm_type": "git", "organization": "Default", "id": 42},
    )
    apply(resource, write=True)
    assert warnings == []
