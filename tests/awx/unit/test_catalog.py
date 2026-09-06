from __future__ import annotations

import pytest

from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure import AwxResourceCatalog
from untaped.capabilities.awx.infrastructure.specs import ALL_SPECS, UNIVERSAL_READ_ONLY


def test_lookup_by_kind() -> None:
    cat = AwxResourceCatalog()
    spec = cat.get("JobTemplate")
    assert spec.cli_name == "job-templates"


def test_lookup_by_cli_name() -> None:
    cat = AwxResourceCatalog()
    spec = cat.by_cli_name("job-templates")
    assert spec.kind == "JobTemplate"


def test_kinds_returns_all() -> None:
    cat = AwxResourceCatalog()
    kinds = cat.kinds()
    assert "JobTemplate" in kinds
    assert "Project" in kinds
    assert "Schedule" in kinds
    assert "WorkflowJobTemplate" in kinds
    assert "Credential" in kinds


def test_unknown_kind_errors() -> None:
    cat = AwxResourceCatalog()
    with pytest.raises(AwxApiError) as exc_info:
        cat.get("NotARealKind")
    assert "JobTemplate" in str(exc_info.value)  # message lists known kinds


def test_credential_is_read_only() -> None:
    cat = AwxResourceCatalog()
    cred = cat.get("Credential")
    assert cred.fidelity == "read_only"
    assert "apply" not in cred.commands
    assert "save" not in cred.commands


def test_workflow_is_partial() -> None:
    cat = AwxResourceCatalog()
    wf = cat.get("WorkflowJobTemplate")
    assert wf.fidelity == "partial"
    assert wf.fidelity_note is not None


def test_schedule_uses_schedule_strategy() -> None:
    cat = AwxResourceCatalog()
    assert cat.get("Schedule").apply_strategy == "schedule"


def test_schedule_parent_is_polymorphic() -> None:
    cat = AwxResourceCatalog()
    parent_fk = next(fk for fk in cat.get("Schedule").fk_refs if fk.field == "parent")
    assert parent_fk.polymorphic
    assert parent_fk.kind_in_value == "kind"


def test_launch_only_kinds_are_registered() -> None:
    """ExecutionEnvironment / Label / InstanceGroup are catalog-only stubs."""
    cat = AwxResourceCatalog()
    for kind in ("ExecutionEnvironment", "Label", "InstanceGroup"):
        spec = cat.get(kind)
        assert spec.kind == kind
        # Stubs aren't CLI-exposed: no save/apply, no list/get sub-app commands.
        assert spec.commands == ()


def test_job_template_launch_fk_refs() -> None:
    """JobTemplate declares the launch-only foreign-key fields."""
    cat = AwxResourceCatalog()
    jt = cat.get("JobTemplate")
    fields = {fk.field: fk for fk in jt.launch_fk_refs}
    assert fields["execution_environment"].kind == "ExecutionEnvironment"
    assert fields["labels"].kind == "Label" and fields["labels"].multi
    assert fields["instance_groups"].kind == "InstanceGroup" and fields["instance_groups"].multi


def test_apply_multi_fk_refs_have_sub_endpoints() -> None:
    """Apply-time multi-FKs must reconcile through explicit sub-endpoints.

    Launch-only multi-FKs are intentionally out of scope: fields like
    JobTemplate.labels are launch payload overrides, not resource-body state.
    """
    jt_launch_fields = {
        fk.field: fk for fk in AwxResourceCatalog().get("JobTemplate").launch_fk_refs
    }
    assert jt_launch_fields["labels"].multi and jt_launch_fields["labels"].sub_endpoint is None

    missing = [
        f"{spec.kind}.{ref.field}"
        for spec in ALL_SPECS
        if "apply" in spec.commands
        for ref in spec.fk_refs
        if ref.multi and ref.sub_endpoint is None
    ]
    assert not missing


def test_specs_without_apply_command_are_read_only() -> None:
    """Every spec opting out of apply via ``commands`` must also be ``read_only``.

    ``application/apply_resource`` gates on ``fidelity == "read_only"`` only —
    if a future spec sets ``commands=("list", "get")`` but ``fidelity="full"``,
    the apply use case would silently issue create/update calls. The CLI-level
    gate (per-kind sub-apps hide ``apply``) is independent, but
    ``untaped awx apply <file>`` flows through the use case directly.
    """
    for spec in ALL_SPECS:
        if "apply" not in spec.commands:
            assert spec.fidelity == "read_only", (
                f"{spec.kind}: 'apply' not in commands but fidelity={spec.fidelity!r}. "
                f"Either add 'apply' to commands or set fidelity='read_only', "
                f"otherwise `untaped awx apply <file>` will issue writes."
            )


def test_non_read_only_specs_expose_save_command() -> None:
    """Top-level ``awx save`` uses fidelity to decide saveability.

    If a future writable spec omitted the per-kind ``save`` command, the
    bulk and per-kind save surfaces would drift. Keep those contracts
    aligned at the spec layer rather than making application code read
    CLI-only ``commands``.
    """
    missing = [
        spec.kind
        for spec in ALL_SPECS
        if spec.fidelity != "read_only" and "save" not in spec.commands
    ]
    assert not missing


def test_saveable_list_columns_are_domain_known_filter_fields() -> None:
    """Bulk-save filter validation runs in application code using only
    domain ``ResourceSpec`` fields. Any displayed list column that users
    reasonably filter on must therefore also be represented by the
    domain-known field set.
    """
    missing: list[str] = []
    for spec in ALL_SPECS:
        if spec.fidelity == "read_only":
            continue
        missing.extend(
            f"{spec.kind}.{column}"
            for column in spec.list_columns
            if column.split("__", 1)[0] not in spec.known_fields
        )

    assert not missing


def test_mutable_specs_declare_universal_read_only_fields() -> None:
    """Read-only stripping in ``ApplyPlanner.plan_payload`` is the safety net
    under the passthrough model: every mutable spec must declare the
    server-managed ``UNIVERSAL_READ_ONLY`` fields so they're never PATCHed back
    (e.g. a stray ``id``/``summary_fields`` from a get-export)."""
    missing: list[str] = []
    for spec in ALL_SPECS:
        if spec.fidelity == "read_only":
            continue
        declared = set(spec.read_only_fields)
        missing.extend(
            f"{spec.kind}.{field}" for field in UNIVERSAL_READ_ONLY if field not in declared
        )

    assert not missing, f"mutable specs missing universal read-only fields: {missing}"
