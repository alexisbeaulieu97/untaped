from __future__ import annotations

import pytest
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure import AwxResourceCatalog


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
