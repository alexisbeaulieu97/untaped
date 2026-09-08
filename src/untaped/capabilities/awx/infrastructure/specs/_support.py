"""FK-only specs: looked up via :class:`FkResolver` but never CRUD'd in v0.

These specs declare just enough for ``name → id`` resolution and
``list/get`` browsing. They omit save/apply because the user explicitly
scoped them out of v0.
"""

from __future__ import annotations

from untaped.capabilities.awx.domain import FkRef
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec

UNIVERSAL_READ_ONLY: tuple[str, ...] = (
    "id",
    "url",
    "type",
    "related",
    "summary_fields",
    "created",
    "modified",
)
"""AWX's server-managed fields present on every resource.

Under the passthrough apply model (``ApplyPlanner.plan_payload``) read-only
stripping is load-bearing — it's what keeps a stray ``id``/``summary_fields``
(e.g. from a get-export) out of a PATCH. Every mutable spec must declare at
least these; ``test_catalog`` pins that invariant.
"""

ORGANIZATION_SPEC = AwxResourceSpec(
    kind="Organization",
    cli_name="organizations",
    api_path="organizations",
    identity_keys=("name",),
    canonical_fields=("description",),
    read_only_fields=(
        "id",
        "created",
        "modified",
        "summary_fields",
        "related",
        "type",
        "url",
    ),
    list_columns=("id", "name"),
    commands=("list", "get"),
    fidelity="read_only",
    fidelity_note="organization CRUD is out of v0 scope",
)


CREDENTIAL_TYPE_SPEC = AwxResourceSpec(
    kind="CredentialType",
    cli_name="credential-types",
    api_path="credential_types",
    identity_keys=("name",),  # CredentialTypes are global (no organization)
    canonical_fields=("description", "kind", "inputs", "injectors"),
    read_only_fields=(
        "id",
        "created",
        "modified",
        "summary_fields",
        "related",
        "type",
        "url",
        "managed",
        "namespace",
    ),
    list_columns=("id", "name", "kind"),
    commands=("list", "get"),
    fidelity="read_only",
    fidelity_note="credential type CRUD is out of v0 scope",
)


# Catalog-only stubs that exist purely so :class:`FkResolver` can map a
# ``name`` to an ``id`` for launch-time foreign keys (referenced by
# ``launch_fk_refs`` on :data:`JOB_TEMPLATE_SPEC` and friends). They have
# no CLI sub-app — ``commands=()`` keeps the resource-app factory from
# generating ``list``/``get``/``save``/``apply``.

EXECUTION_ENVIRONMENT_SPEC = AwxResourceSpec(
    kind="ExecutionEnvironment",
    cli_name="execution-environments",
    api_path="execution_environments",
    identity_keys=("name",),
    canonical_fields=("description", "image"),
    read_only_fields=("id", "created", "modified", "summary_fields", "related", "type", "url"),
    list_columns=("id", "name", "image"),
    commands=(),
    fidelity="read_only",
    fidelity_note="execution environment CRUD is out of v0 scope",
)


LABEL_SPEC = AwxResourceSpec(
    kind="Label",
    cli_name="labels",
    api_path="labels",
    identity_keys=("name", "organization"),
    canonical_fields=("name",),
    read_only_fields=("id", "created", "modified", "summary_fields", "related", "type", "url"),
    fk_refs=(FkRef(field="organization", kind="Organization"),),
    list_columns=("id", "name", "organization"),
    commands=(),
    fidelity="read_only",
    fidelity_note="label CRUD is out of v0 scope",
)


INSTANCE_GROUP_SPEC = AwxResourceSpec(
    kind="InstanceGroup",
    cli_name="instance-groups",
    api_path="instance_groups",
    identity_keys=("name",),  # globally unique in AWX
    canonical_fields=("name",),
    read_only_fields=("id", "created", "modified", "summary_fields", "related", "type", "url"),
    list_columns=("id", "name"),
    commands=(),
    fidelity="read_only",
    fidelity_note="instance group CRUD is out of v0 scope",
)
