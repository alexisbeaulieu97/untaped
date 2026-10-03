"""``WorkflowJobTemplate`` documents carry their node graph: export, apply, round trip."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.testing import CliInvoker
from untaped_awx.cli import app

# Export order: from the roots down, ties by id.
_GRAPH = [
    {
        "id": "approve-prod",
        "approval": {"name": "Approve production", "timeout": 3600},
        "success": ["deploy"],
    },
    {
        "id": "deploy",
        "run": {"job_template": "Deploy"},
        "prompts": {
            "inventory": "Production",
            "credentials": ["ssh"],
            "extra_vars": {"version": 3},
            "limit": "web*",
        },
        "success": ["verify"],
        "failure": ["rollback"],
    },
    {"id": "rollback", "run": {"job_template": "Shared rollback", "organization": "Ops"}},
    {
        "id": "verify",
        "run": {"job_template": "Smoke check"},
        "all_parents_must_converge": True,
    },
]


def _seed(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed("organizations", id=2, name="Ops")
    fake.seed("inventories", id=20, name="Production", organization=1, kind="")
    fake.seed("credentials", id=40, name="ssh", organization=1, credential_type=1)
    fake.seed("job_templates", id=10, name="Deploy", organization=1)
    fake.seed("job_templates", id=11, name="Smoke check", organization=1)
    fake.seed("job_templates", id=12, name="Shared rollback", organization=2)
    fake.seed(
        "workflow_job_templates",
        id=100,
        name="Release",
        organization=1,
        description="Build, deploy, verify",
    )
    fake.seed("workflow_approval_templates", id=300, name="Approve production", timeout=3600)
    nodes = {
        201: {"identifier": "deploy", "unified_job_template": 10, "inventory": 20,
              "limit": "web*", "extra_data": {"version": 3},
              "success_nodes": [203], "failure_nodes": [204]},
        202: {"identifier": "approve-prod", "unified_job_template": 300, "success_nodes": [201]},
        203: {"identifier": "verify", "unified_job_template": 11,
              "all_parents_must_converge": True},
        204: {"identifier": "rollback", "unified_job_template": 12},
    }  # fmt: skip
    for node_id, fields in nodes.items():
        fake.seed("workflow_nodes", id=node_id, workflow_job_template=100, **fields)
    fake.memberships[("workflow_job_template_nodes", 201, "credentials")] = {40}


def _invoke(*args: str, input: str | None = None) -> Any:
    return CliInvoker().invoke(app, list(args), input=input)


def _export(name: str = "Release") -> dict[str, Any]:
    result = _invoke("workflow-templates", "export", name, "--organization", "Default")
    assert result.exit_code == 0, result.output + (result.stderr or "")
    document = yaml.safe_load(result.stdout)
    assert isinstance(document, dict)
    return document


def _write(tmp_path: Path, *documents: dict[str, Any]) -> Path:
    path = tmp_path / "docs.yml"
    path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
    return path


def _apply(path: Path, *flags: str) -> Any:
    return _invoke("apply", str(path), *flags)


def _nodes(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A document's nodes by id; editing them edits the document."""
    return {node["id"]: node for node in document["spec"]["nodes"]}


def _stderr_lines(result: Any) -> list[str]:
    return (result.stderr or "").splitlines()


def _workflow(fake: Any, name: str) -> dict[str, Any]:
    return next(r for r in fake.list_records("workflow_job_templates") if r["name"] == name)


def _writes(fake: Any) -> list[Any]:
    return [call for call in fake.router.calls if call.request.method != "GET"]


def test_export_writes_the_full_graph_by_name(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = _invoke("workflow-templates", "export", "Release", "--organization", "Default")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert not result.stdout.startswith("#")
    assert "partial" not in (result.stderr or "")
    assert yaml.safe_load(result.stdout)["spec"]["nodes"] == _GRAPH


def test_reapplying_an_export_changes_nothing(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    path = _write(tmp_path, _export())

    result = _apply(path, "--yes", "--format", "json")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert '"unchanged"' in result.stdout
    assert not _writes(fake_aap)


def test_export_applied_under_a_new_name_rebuilds_the_graph(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    document = _export()
    document["metadata"]["name"] = "Release copy"

    result = _apply(_write(tmp_path, document), "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    copied = _export("Release copy")
    assert copied["spec"] == document["spec"]
    approval = next(
        r
        for r in fake_aap.list_records("workflow_approval_templates")
        if r["id"] != 300 and r["name"] == "Approve production"
    )
    assert approval["timeout"] == 3600


def test_changes_are_previewed_by_node_then_reconciled(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    document = _export()
    nodes = {node["id"]: node for node in document["spec"]["nodes"]}
    nodes["deploy"]["prompts"]["limit"] = "web1"
    del nodes["deploy"]["failure"]
    nodes["approve-prod"]["approval"]["timeout"] = 600
    nodes["verify"]["always"] = ["notify"]
    del nodes["rollback"]
    nodes["notify"] = {"id": "notify", "run": {"job_template": "Deploy"}}
    document["spec"]["nodes"] = list(nodes.values())
    path = _write(tmp_path, document)

    preview = _apply(path, "--dry-run")

    assert preview.exit_code == 0, preview.output + (preview.stderr or "")
    lines = (preview.stderr or "").splitlines()
    for expected in (
        '  nodes[notify]: null → {"run":{"job_template":"Deploy"}} (create)',
        '  nodes[deploy].prompts.limit: "web*" → "web1"',
        "  nodes[approve-prod].approval: "
        '{"name":"Approve production","timeout":3600} → '
        '{"name":"Approve production","timeout":600}',
        '  nodes[rollback]: {"run":{"job_template":"Shared rollback","organization":"Ops"}}'
        " → null (delete)",
        '  nodes[verify].always: [] → ["notify"]',
    ):
        assert expected in lines, preview.stderr
    assert not _writes(fake_aap)

    result = _apply(path, "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert _export()["spec"]["nodes"] == document["spec"]["nodes"]
    assert 204 not in fake_aap.store["workflow_nodes"]
    assert fake_aap.get_record("workflow_approval_templates", 300)["timeout"] == 600


def test_a_node_switching_to_an_approval_is_replaced(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    document = _export()
    deploy = _nodes(document)["deploy"]
    del deploy["run"], deploy["prompts"]
    deploy["approval"] = {"name": "Looks good?"}
    path = _write(tmp_path, document)

    preview = _apply(path, "--dry-run")

    assert preview.exit_code == 0, preview.output + (preview.stderr or "")
    rows = [line for line in _stderr_lines(preview) if line.startswith("  nodes[")]
    # Unchanged edges of the replaced node and into it are not changes.
    assert rows == [
        '  nodes[deploy]: {"run":{"job_template":"Deploy"},"prompts":{"inventory":"Production",'
        '"credentials":["ssh"],"extra_vars":{"version":3},"limit":"web*"}} → '
        '{"approval":{"name":"Looks good?"}} (replace)'
    ]

    result = _apply(path, "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert 201 not in fake_aap.store["workflow_nodes"]
    assert _export()["spec"]["nodes"] == document["spec"]["nodes"]


def test_templates_in_the_same_batch_are_created_before_the_workflow(
    fake_aap: Any, tmp_path: Path
) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    workflow = {
        "kind": "WorkflowJobTemplate",
        "metadata": {"name": "Pipeline", "organization": "Default"},
        "spec": {"nodes": [{"id": "build", "run": {"job_template": "Build"}}]},
    }
    template = {
        "kind": "JobTemplate",
        "metadata": {"name": "Build", "organization": "Default"},
        "spec": {"playbook": "build.yml"},
    }

    result = _apply(_write(tmp_path, workflow, template), "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    build = next(r for r in fake_aap.list_records("job_templates") if r["name"] == "Build")
    pipeline = _workflow(fake_aap, "Pipeline")
    (node,) = fake_aap.list_records("workflow_nodes")
    assert node["workflow_job_template"] == pipeline["id"]
    assert node["unified_job_template"] == build["id"]
    assert build["id"] < pipeline["id"]


@pytest.mark.parametrize(
    ("nodes", "message"),
    [
        ([{"id": "a", "run": {"job_template": "Deploy"}, "success": ["b"]}], "unknown node id"),
        (
            [
                {"id": "a", "run": {"job_template": "Deploy"}, "success": ["b"]},
                {"id": "b", "run": {"job_template": "Deploy"}, "success": ["a"]},
            ],
            "nodes form a cycle: a → b → a",
        ),
        (
            [{"id": "a", "run": {"job_template": "Deploy"}}] * 2,
            "duplicate node id 'a'",
        ),
        (
            [{"id": "a", "run": {"job_template": "Deplyo"}}],
            "did you mean",
        ),
    ],
)
def test_invalid_graphs_are_refused_before_any_write(
    fake_aap: Any, tmp_path: Path, nodes: list[dict[str, Any]], message: str
) -> None:
    _seed(fake_aap)
    document = {
        "kind": "WorkflowJobTemplate",
        "metadata": {"name": "Release", "organization": "Default"},
        "spec": {"description": "changed", "nodes": copy.deepcopy(nodes)},
    }

    result = _apply(_write(tmp_path, document), "--yes")

    assert result.exit_code == 1
    assert message in result.output + (result.stderr or "")
    assert not _writes(fake_aap)


def _seed_more(fake: Any) -> None:
    """Every run kind and every by-name prompt, one of them in another organization."""
    _seed(fake)
    fake.seed("projects", id=13, name="playbooks", organization=1)
    fake.seed("inventories", id=21, name="Cloud", organization=2, kind="")
    fake.seed("inventory_sources", id=14, name="aws", inventory=21)
    fake.seed("workflow_job_templates", id=15, name="Nightly", organization=1)
    fake.seed("labels", id=50, name="release", organization=1)
    fake.seed("instance_groups", id=60, name="edge")
    fake.seed("instance_groups", id=61, name="default")
    fake.seed("execution_environments", id=70, name="ee-supported")
    fake.seed("credentials", id=41, name="vault", organization=2, credential_type=3)
    extra = {
        205: {"identifier": "sync-project", "unified_job_template": 13, "success_nodes": [206]},
        206: {"identifier": "sync-cloud", "unified_job_template": 14, "always_nodes": [207]},
        207: {"identifier": "nightly", "unified_job_template": 15, "execution_environment": 70,
              "job_type": "check", "verbosity": 2, "diff_mode": False, "forks": 5,
              "job_slice_count": 2, "timeout": 60, "scm_branch": "main",
              "job_tags": "a", "skip_tags": "b"},
    }  # fmt: skip
    for node_id, fields in extra.items():
        fake.seed("workflow_nodes", id=node_id, workflow_job_template=100, **fields)
    fake.memberships[("workflow_job_template_nodes", 207, "labels")] = {50}
    fake.memberships[("workflow_job_template_nodes", 207, "instance_groups")] = {61, 60}
    fake.memberships[("workflow_job_template_nodes", 207, "credentials")] = {41}
    # A management job, and a template without an organization.
    fake.seed("system_job_templates", id=16, name="Cleanup Job Details")
    fake.seed("job_templates", id=17, name="Global cleanup", organization=None)
    fake.seed("workflow_nodes", id=208, workflow_job_template=100, identifier="cleanup",
              unified_job_template=16, extra_data={"days": 30})  # fmt: skip
    fake.seed("workflow_nodes", id=210, workflow_job_template=100, identifier="global",
              unified_job_template=17)  # fmt: skip


def test_every_run_kind_and_prompt_round_trips(fake_aap: Any, tmp_path: Path) -> None:
    _seed_more(fake_aap)
    document = _export()
    nodes = {node["id"]: node for node in document["spec"]["nodes"]}
    assert nodes["sync-project"]["run"] == {"project": "playbooks"}
    assert nodes["cleanup"]["run"] == {"system_job_template": "Cleanup Job Details"}
    assert nodes["global"]["run"] == {"job_template": "Global cleanup", "organization": None}
    assert nodes["sync-cloud"]["run"] == {
        "inventory_source": "aws",
        "organization": "Ops",
        "inventory": "Cloud",
    }
    assert nodes["nightly"]["prompts"] == {
        "credentials": [{"name": "vault", "organization": "Ops"}],
        "labels": ["release"],
        "instance_groups": ["edge", "default"],
        "execution_environment": "ee-supported",
        "scm_branch": "main",
        "job_tags": "a",
        "skip_tags": "b",
        "job_type": "check",
        "verbosity": 2,
        "diff_mode": False,
        "forks": 5,
        "job_slice_count": 2,
        "timeout": 60,
    }
    document["metadata"]["name"] = "Release copy"

    result = _apply(_write(tmp_path, document), "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert _export("Release copy")["spec"] == document["spec"]


def test_edges_move_templates_change_and_groups_reorder(fake_aap: Any, tmp_path: Path) -> None:
    _seed_more(fake_aap)
    document = _export()
    nodes = {node["id"]: node for node in document["spec"]["nodes"]}
    nodes["deploy"]["run"] = {"job_template": "Smoke check"}
    del nodes["deploy"]["success"]
    nodes["approve-prod"]["success"] = ["deploy", "verify"]
    nodes["nightly"]["prompts"]["instance_groups"] = ["default", "edge"]
    path = _write(tmp_path, document)

    result = _apply(path, "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert _export()["spec"]["nodes"] == document["spec"]["nodes"]
    assert fake_aap.get_record("workflow_nodes", 201)["unified_job_template"] == 11
    assert list(fake_aap.memberships[("workflow_job_template_nodes", 207, "instance_groups")]) == [
        61,
        60,
    ]
    edge_posts = [
        call.request.content
        for call in fake_aap.router.calls
        if call.request.method == "POST" and call.request.url.path.endswith("_nodes/")
    ]
    assert edge_posts == [b'{"id":203,"disassociate":true}', b'{"id":203}']


def test_node_relations_an_older_controller_lacks_export_empty(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.missing_sub_paths.update(
        {
            ("workflow_job_template_nodes", "labels"),
            ("workflow_job_template_nodes", "instance_groups"),
        }
    )

    assert _export()["spec"]["nodes"] == _GRAPH


def test_check_reports_node_drift(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    document = _export()
    _nodes(document)["deploy"]["prompts"]["limit"] = "db*"

    result = _apply(_write(tmp_path, document), "--check")

    assert result.exit_code == 3, result.output + (result.stderr or "")
    assert not _writes(fake_aap)


def test_a_refused_node_write_leaves_a_partial_row(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    fake_aap.seed("credentials", id=42, name="deploy-key", organization=1, credential_type=2)
    fake_aap.forbidden_associate_ids.add(42)
    document = _export()
    document["spec"]["description"] = "changed"
    _nodes(document)["deploy"]["prompts"]["credentials"] = ["ssh", "deploy-key"]

    result = _apply(_write(tmp_path, document), "--yes", "--format", "json")

    assert result.exit_code == 4  # AWX refused a permission: fix the environment
    assert '"partial"' in result.stdout
    assert "workflow nodes failed: nodes[deploy] update:" in result.stdout
    [row] = [row for row in json.loads(result.stdout) if row["action"] == "partial"]
    assert (row["error"]["category"], row["error"]["system"]) == ("permission", "awx")
    assert _workflow(fake_aap, "Release")["description"] == "changed"


@pytest.mark.parametrize(
    ("credential_type", "outcome"),
    [(2, "permission denied"), (1, "restored removed members 40")],
)
def test_a_refused_node_credential_swap_keeps_the_old_credential(
    fake_aap: Any, tmp_path: Path, credential_type: int, outcome: str
) -> None:
    """Adds come first; a same-type swap removes first and restores on refusal."""
    _seed(fake_aap)
    fake_aap.seed("credentials", id=42, name="new", organization=1, credential_type=credential_type)
    fake_aap.forbidden_associate_ids.add(42)
    document = _export()
    _nodes(document)["deploy"]["prompts"]["credentials"] = ["new"]

    result = _apply(_write(tmp_path, document), "--yes", "--format", "json")

    assert result.exit_code == 4  # AWX refused a permission
    assert outcome in result.stdout
    assert set(fake_aap.memberships[("workflow_job_template_nodes", 201, "credentials")]) == {40}


def test_encrypted_node_extra_vars_are_dropped_on_create_and_match_on_update(
    fake_aap: Any, tmp_path: Path
) -> None:
    _seed(fake_aap)
    fake_aap.get_record("workflow_nodes", 201)["extra_data"] = {
        "version": 3,
        "password": "$encrypted$",
    }
    document = _export()
    assert _nodes(document)["deploy"]["prompts"]["extra_vars"]["password"] == "$encrypted$"

    # A plaintext secret in the document matches the stored (masked) one.
    _nodes(document)["deploy"]["prompts"]["extra_vars"]["password"] = "s3cret"
    same = _apply(_write(tmp_path, document), "--yes", "--format", "json")
    assert same.exit_code == 0, same.output + (same.stderr or "")
    assert '"unchanged"' in same.stdout

    # A placeholder cannot be sent to a new node: it is dropped with a warning.
    _nodes(document)["deploy"]["prompts"]["extra_vars"]["password"] = "$encrypted$"
    document["metadata"]["name"] = "Release copy"
    copied = _apply(_write(tmp_path, document), "--yes")
    assert copied.exit_code == 0, copied.output + (copied.stderr or "")
    assert "nodes[deploy] extra_vars.password is $encrypted$" in (copied.stderr or "")
    assert _nodes(_export("Release copy"))["deploy"]["prompts"]["extra_vars"] == {"version": 3}


def test_orphaned_nodes_are_left_out_of_exports_and_left_alone(
    fake_aap: Any, tmp_path: Path
) -> None:
    _seed(fake_aap)
    fake_aap.seed("workflow_nodes", id=209, workflow_job_template=100, identifier="orphan")
    fake_aap.get_record("workflow_nodes", 203)["success_nodes"] = [209]

    result = _invoke("workflow-templates", "export", "Release", "--organization", "Default")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "node 'orphan' runs nothing" in (result.stderr or "")
    assert yaml.safe_load(result.stdout)["spec"]["nodes"] == _GRAPH
    reapplied = _apply(_write(tmp_path, yaml.safe_load(result.stdout)), "--yes")
    assert reapplied.exit_code == 0, reapplied.output + (reapplied.stderr or "")
    assert not _writes(fake_aap)
    assert fake_aap.get_record("workflow_nodes", 203)["success_nodes"] == [209]

    out_dir = tmp_path / "backup"
    bulk = _invoke("export", "--kind", "workflow-templates", "--out-dir", str(out_dir))
    assert bulk.exit_code == 0, bulk.output + (bulk.stderr or "")
    assert (out_dir / "WorkflowJobTemplate__Default__Release.yml").exists()


def test_workflows_that_run_each_other_are_refused(fake_aap: Any, tmp_path: Path) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    documents = [
        {
            "kind": "WorkflowJobTemplate",
            "metadata": {"name": name, "organization": "Default"},
            "spec": {"nodes": [{"id": "x", "run": {"workflow_job_template": other}}]},
        }
        for name, other in (("A", "B"), ("B", "A"))
    ]

    result = _apply(_write(tmp_path, *documents), "--yes")

    assert result.exit_code == 1
    assert "workflow nodes form a recursion: A → B → A" in (result.stderr or "")
    assert not _writes(fake_aap)


def test_member_reads_happen_once_per_apply(fake_aap: Any, tmp_path: Path) -> None:
    """Preflight and verification reuse the plan's member reads for untouched nodes."""
    _seed(fake_aap)
    document = _export()
    _nodes(document)["deploy"]["prompts"]["limit"] = "db*"
    path = _write(tmp_path, document)
    fake_aap.router.reset()

    result = _apply(path, "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    member_reads = [
        call
        for call in fake_aap.router.calls
        if call.request.method == "GET"
        and call.request.url.path.endswith(("/credentials/", "/labels/", "/instance_groups/"))
        and "workflow_job_template_nodes" in call.request.url.path
    ]
    assert len(member_reads) == 3 * 4


def test_an_ignored_node_write_is_reported_unconverged(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    fake_aap.ignored_write_fields.add("limit")
    document = _export()
    _nodes(document)["deploy"]["prompts"]["limit"] = "db*"

    result = _apply(_write(tmp_path, document), "--yes", "--format", "json")

    assert result.exit_code == 1
    assert "did not converge: nodes[deploy].prompts.limit" in result.stdout


def test_nodes_changed_after_planning_are_a_conflict(fake_aap: Any) -> None:
    from untaped_awx.application import BatchMutationEngine
    from untaped_awx.cli.context import open_context
    from untaped_awx.domain import Resource

    _seed(fake_aap)
    with open_context() as ctx:
        document = Resource.model_validate(_export())
        _nodes({"spec": document.spec})["deploy"]["prompts"]["limit"] = "db*"
        engine = BatchMutationEngine(
            ctx.repo, ctx.catalog, ctx.fk, ctx.strategies, nodes=ctx.workflow_nodes
        )
        plan = engine.prepare([document])
        fake_aap.get_record("workflow_nodes", 203)["limit"] = "edited elsewhere"

        (outcome,) = engine.execute(plan).outcomes

    assert outcome.action == "conflict"
    assert "changed its workflow nodes after planning" in (outcome.detail or "")
    assert fake_aap.get_record("workflow_nodes", 201)["limit"] == "web*"
