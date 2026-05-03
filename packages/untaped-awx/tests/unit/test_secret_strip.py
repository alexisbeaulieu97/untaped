"""Pin wildcard secret-stripping behaviour in ApplyResource.

The only wildcard secret-path that fires through ``ApplyResource`` today
is JobTemplate / Workflow's
``survey_spec.spec.*.default``. ``$encrypted$`` placeholders inside a
list element produce a preserved path containing a literal ``*``, which
exercises the intermediate-``*`` branches of ``_remove_at_path``
(``application/apply_resource.py:409-411``) when comparing the user
payload against the existing record's stripped subtree.

If the wildcard handling regresses, AWX would either reject the request
(400) or store the literal placeholder. The test asserts the public
observable: what the strategy's ``update`` is called with, and what the
``ApplyOutcome`` reports as preserved. Private helpers can be
refactored freely as long as the contract holds.

``CREDENTIAL_SPEC.secret_paths = ("inputs.*",)`` declares a terminal
wildcard pattern but ``CREDENTIAL`` is ``fidelity="read_only"`` today,
so ``ApplyResource`` refuses it. The ``inputs.*`` pattern is dormant
until credentials gain apply support; tests for it can land alongside
that change.

The stub Protocols mirror ``test_apply_resource.py``; we copy them here
because pytest's ``--import-mode=importlib`` disallows cross-test-file
imports per the project's test layout (see AGENTS.md "Test layout").
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped_awx.application import ApplyResource
from untaped_awx.domain import Metadata, Resource, ResourceSpec
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC

# ----- Stubs (copies of those in test_apply_resource.py) -----


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
    def list(
        self, spec: ResourceSpec, *, params: Any = None, limit: Any = None
    ) -> Iterator[dict[str, Any]]:
        return iter([])

    def get(self, spec: ResourceSpec, id_: int) -> dict[str, Any]:
        raise NotImplementedError

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> dict[str, Any] | None:
        return None

    def create(self, spec: ResourceSpec, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def update(self, spec: ResourceSpec, id_: int, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

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
    def __init__(self, existing: dict[str, Any] | None) -> None:
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
) -> ApplyResource:
    return ApplyResource(
        client=_StubClient(),  # type: ignore[arg-type]
        catalog=_StubCatalog(catalog_specs),  # type: ignore[arg-type]
        fk=_StubFk(fk_names),  # type: ignore[arg-type]
        strategies=_StubStrategies(strategy),  # type: ignore[arg-type]
        warn=lambda _msg: None,
    )


# ----- Tests -----


def test_intermediate_list_wildcard_preserves_survey_default_under_sibling_change() -> None:
    """``survey_spec.spec.*.default`` declared on JT: ``$encrypted$`` inside
    each list element produces a preserved path with a literal ``*``.
    ``_strip_paths`` walks that wildcard against the existing record so
    the equality check finds a match and the field is preserved (not
    flagged as conflict). The user can edit a sibling top-level field
    and the PATCH excludes ``survey_spec`` entirely.

    Exercises ``_remove_at_path`` lines 409-411 (intermediate ``*``
    walking list items).
    """
    existing = {
        "id": 7,
        "name": "deploy",
        "organization": 1,
        "playbook": "deploy.yml",
        "description": "old",
        "survey_spec": {
            "spec": [
                {
                    "variable": "pw",
                    "default": "$encrypted$",
                    "question_name": "Password",
                },
                {
                    "variable": "env",
                    "default": "$encrypted$",
                    "question_name": "Environment",
                },
            ],
        },
    }
    strategy = _StubStrategy(existing=existing)
    apply = _make_apply(
        catalog_specs={"JobTemplate": JOB_TEMPLATE_SPEC},
        fk_names={("Organization", "Default"): 1},
        strategy=strategy,
    )
    # User changes `description` (sibling top-level field). The survey is
    # identical to existing — still carrying placeholders, as a
    # save-then-apply round-trip would.
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={
            "playbook": "deploy.yml",
            "description": "new",
            "survey_spec": {
                "spec": [
                    {
                        "variable": "pw",
                        "default": "$encrypted$",
                        "question_name": "Password",
                    },
                    {
                        "variable": "env",
                        "default": "$encrypted$",
                        "question_name": "Environment",
                    },
                ],
            },
        },
    )
    outcome = apply(resource, write=True)

    assert outcome.action == "updated"
    # Both placeholders are reported as preserved (one per list item).
    assert any(p.startswith("survey_spec.spec.") for p in outcome.preserved_secrets)
    assert strategy.updated is not None
    _, patch_payload = strategy.updated
    # `survey_spec` is preserved (omitted from PATCH; no `$encrypted$` leaks).
    assert "survey_spec" not in patch_payload
    assert patch_payload.get("description") == "new"
    assert _contains_encrypted_placeholder(patch_payload) is False


def test_intermediate_list_wildcard_blocks_sibling_change_inside_survey() -> None:
    """If the user changes the survey's structure alongside a placeholder
    (e.g. renames ``question_name`` while keeping ``default: $encrypted$``),
    the apply pipeline refuses with ``BadRequest``. PATCHing the new
    structure would clobber AWX's stored encrypted value.

    Pins the conflict-detection behaviour for the wildcard path.
    """
    from untaped_awx.errors import BadRequest

    existing = {
        "id": 7,
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
            ],
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
                        "question_name": "Renamed Question",  # sibling change
                    },
                ],
            },
        },
    )
    import pytest as _pytest

    with _pytest.raises(BadRequest, match="survey_spec"):
        apply(resource, write=True)


# ----- helpers -----


def _contains_encrypted_placeholder(obj: Any) -> bool:
    """Recursive search for the ``$encrypted$`` sentinel anywhere in obj."""
    if isinstance(obj, str):
        return obj == "$encrypted$"
    if isinstance(obj, dict):
        return any(_contains_encrypted_placeholder(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_encrypted_placeholder(v) for v in obj)
    return False
