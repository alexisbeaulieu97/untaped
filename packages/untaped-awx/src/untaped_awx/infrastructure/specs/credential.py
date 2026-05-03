"""Credential: read-only in v0.

The ``$encrypted$`` placeholder problem (live secrets cannot be read
back) makes save/apply roundtripping a separate design effort. Listing
and getting are still useful: they let users browse existing
credentials and copy IDs into manifests.
"""

from __future__ import annotations

from untaped_awx.domain import FkRef
from untaped_awx.infrastructure.spec import AwxResourceSpec

CREDENTIAL_SPEC = AwxResourceSpec(
    kind="Credential",
    cli_name="credentials",
    api_path="credentials",
    identity_keys=("name", "organization"),
    canonical_fields=(
        "description",
        "organization",
        "credential_type",
        "inputs",
    ),
    read_only_fields=(
        "id",
        "created",
        "modified",
        "summary_fields",
        "related",
        "type",
        "url",
        "kind",
        "cloud",
        "kubernetes",
        "managed",
    ),
    fk_refs=(
        FkRef(field="organization", kind="Organization"),
        FkRef(field="credential_type", kind="CredentialType"),  # global; no scope
    ),
    secret_paths=("inputs.*",),
    list_columns=("name", "organization", "credential_type"),
    commands=("list", "get"),
    fidelity="read_only",
    fidelity_note="$encrypted$ roundtrip deferred to v0.5",
)
