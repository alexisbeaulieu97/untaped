"""Credential: read-only (list and get).

The ``$encrypted$`` placeholder problem (live secrets cannot be read
back) makes save/apply roundtripping a separate design effort. Listing
and getting are still useful: they let users browse existing
credentials and copy IDs into manifests.
"""

from __future__ import annotations

from untaped_awx.domain import FkRef
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.infrastructure.resources.specs._support import UNIVERSAL_READ_ONLY
from untaped_awx.records import Credential

CREDENTIAL_SPEC = AwxResourceSpec(
    kind="Credential",
    record=Credential,
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
        *UNIVERSAL_READ_ONLY,
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
    list_columns=("id", "name", "credential_type"),
    commands=("list", "get"),
    fidelity="read_only",
    fidelity_note="AWX returns its secrets as $encrypted$, so it cannot be applied back",
)
