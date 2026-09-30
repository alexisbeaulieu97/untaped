"""Temporary test sets: binding suites to repo specs, and the copies a run creates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from untaped.capabilities.awx.domain import Resource
from untaped.capabilities.awx.domain.suite import Case, Suite, TemplateBinding, select_cases
from untaped.capabilities.awx.domain.temporary_set import (
    Marker,
    leftover,
    parse_age,
    plan_temporary_set,
    temporary_name,
)
from untaped.capabilities.awx.infrastructure import AwxResourceCatalog
from untaped.capability_api import ConfigError

SHA = "1a2b3c4d5e6f7a8b9c0d1a2b3c4d5e6f7a8b9c0d"
CREATED = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
MARKER = Marker(run_id="k3x9", ref="feature/x", sha=SHA[:7], created=CREATED)


def _doc(kind: str, name: str, organization: str | None = "Default", **spec: Any) -> Resource:
    return Resource.model_validate(
        {"kind": kind, "metadata": {"name": name, "organization": organization}, "spec": spec}
    )


def _suite(name: str, cases: dict[str, dict[str, Any]], **body: Any) -> Suite:
    body.setdefault("jobTemplate", "Deploy")
    return Suite.model_validate({"kind": "AwxTestSuite", "name": name, **body, "cases": cases})


def _plan(suites: list[Suite], specs: list[Resource], case_filter: set[str] | None = None) -> Any:
    return plan_temporary_set(
        select_cases(suites, case_filter),
        [(f"feature/x:.untaped/awx/{doc.metadata.name}.yml", doc) for doc in specs],
        marker=MARKER,
        sha=SHA,
        default_scope={"organization": "Default"},
        kinds=AwxResourceCatalog().get,
    )


def test_a_temporary_name_carries_the_commit_and_the_run() -> None:
    assert temporary_name("Deploy", SHA, "k3x9") == "Deploy [untaped-test 1a2b3c4 k3x9]"


def test_a_marker_round_trips_through_a_description() -> None:
    text = MARKER.render()

    assert text == "untaped-test run=k3x9 ref=feature/x sha=1a2b3c4 created=2026-09-29T12:00:00Z"
    assert Marker.parse(text) == MARKER
    assert Marker.parse("Deploys the app") is None
    assert Marker.parse("untaped-test run=k3x9 ref=x sha=1a2b3c4 created=yesterday") is None


def test_a_leftover_needs_both_its_name_and_its_marker() -> None:
    name = "Deploy [untaped-test 1a2b3c4 k3x9]"

    assert leftover(name, MARKER.render()) == MARKER
    assert leftover("Deploy", MARKER.render()) is None
    assert leftover(name, "a description someone wrote") is None
    # A copy of another run's object is not this marker's.
    assert leftover("Deploy [untaped-test 1a2b3c4 zzzz]", MARKER.render()) is None


@pytest.mark.parametrize(
    ("text", "age"),
    [
        ("2h", timedelta(hours=2)),
        ("30m", timedelta(minutes=30)),
        ("1d", timedelta(days=1)),
        ("90s", timedelta(seconds=90)),
        ("0", timedelta(0)),
    ],
)
def test_ages_read_as_a_number_and_a_unit(text: str, age: timedelta) -> None:
    assert parse_age(text) == age


@pytest.mark.parametrize("text", ["", "2", "h", "2w", "-1h", "1.5h"])
def test_other_ages_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="like 2h"):
        parse_age(text)


def test_a_suite_whose_template_has_a_spec_is_bound_to_a_pinned_copy() -> None:
    spec = _doc(
        "JobTemplate",
        "Deploy",
        project="Playbooks",
        playbook="deploy.yml",
        description="Deploys",
        scm_branch="main",
        ask_inventory_on_launch=True,
        webhook_service="github",
        webhook_credential="Hook",
        webhook_key="$encrypted$",
    )
    suite = _suite(
        "deploy",
        {"web": {"launch": {"limit": "web", "scm_branch": "x"}}, "db": {"launch": {}}},
        defaults={"launch": {"inventory": "Test", "extra_vars": {"a": 1}}},
    )

    plan = _plan([suite, _suite("other", {"c": {}}, jobTemplate="Other")], [spec])

    [copy] = plan.templates
    name = "Deploy [untaped-test 1a2b3c4 k3x9]"
    assert (copy.kind, copy.source, copy.organization, copy.name) == (
        "JobTemplate",
        "Deploy",
        "Default",
        name,
    )
    assert copy.path == "feature/x:.untaped/awx/Deploy.yml"
    # Every field a case sets at launch prompts, except the ref the copy pins;
    # only the prompts the spec did not already enable are listed.
    assert copy.prompts == ("ask_limit_on_launch", "ask_variables_on_launch")
    assert copy.document.metadata.name == name
    assert copy.document.metadata.organization == "Default"
    assert copy.document.spec == {
        "project": "Playbooks",
        "playbook": "deploy.yml",
        "description": MARKER.render(),
        "scm_branch": SHA,
        "ask_inventory_on_launch": True,
        "ask_limit_on_launch": True,
        "ask_variables_on_launch": True,
    }
    assert plan.bindings == {
        "deploy": TemplateBinding("JobTemplate", name, {"organization": "Default"}, pinned=True)
    }


def test_a_spec_in_another_organization_does_not_bind() -> None:
    spec = _doc("JobTemplate", "Deploy", organization="Ops")

    plan = _plan([_suite("deploy", {"c": {}})], [spec])

    assert (plan.templates, plan.bindings) == ((), {})
    assert plan.notes == (
        "deploy: the spec of JobTemplate 'Deploy' (feature/x:.untaped/awx/Deploy.yml) is not "
        "in organization Default, so the suite runs the template AWX holds",
    )


def test_a_node_naming_a_template_of_another_organization_keeps_its_name() -> None:
    """Only the node running the copied template (same name *and* organization) is renamed."""
    workflow = _doc(
        "WorkflowJobTemplate",
        "Release",
        nodes=[
            {"id": "here", "run": {"job_template": "Deploy"}},
            {"id": "there", "run": {"job_template": "Deploy", "organization": "Ops"}},
        ],
    )
    suite = _suite("release", {"c": {}}, jobTemplate=None, workflowTemplate="Release")

    plan = _plan([suite], [workflow, _doc("JobTemplate", "Deploy")])

    nodes = plan.templates[1].document.spec["nodes"]
    assert [node["run"] for node in nodes] == [
        {"job_template": "Deploy [untaped-test 1a2b3c4 k3x9]"},
        {"job_template": "Deploy", "organization": "Ops"},
    ]
    assert [target.key for target in plan.templates[1].targets] == [
        ("JobTemplate", "Deploy", "Default"),
        ("JobTemplate", "Deploy", "Ops"),
    ]


def test_approval_nodes_include_those_of_nested_copies() -> None:
    outer = _doc(
        "WorkflowJobTemplate",
        "Release",
        nodes=[
            {"id": "gate", "approval": {"name": "Go"}},
            {"id": "inner", "run": {"workflow_job_template": "Inner"}},
        ],
    )
    inner = _doc(
        "WorkflowJobTemplate", "Inner", nodes=[{"id": "second", "approval": {"name": "Again"}}]
    )
    suite = _suite("release", {"c": {}}, jobTemplate=None, workflowTemplate="Release")

    plan = _plan([suite], [outer, inner])

    copy = plan.bound(plan.bindings["release"])
    assert copy is not None and copy.source == "Release"
    assert plan.approval_nodes(copy) == ["gate", "inner/second"]


def test_a_suite_without_selected_cases_provisions_nothing() -> None:
    spec = _doc("JobTemplate", "Deploy")

    plan = _plan(
        [_suite("deploy", {"c": {}}), _suite("b", {"d": {}}, jobTemplate="B")],
        [spec],
        case_filter={"b/d"},
    )

    assert plan.templates == ()


def test_two_specs_matching_one_suite_are_refused() -> None:
    specs = [_doc("JobTemplate", "Deploy", organization="Ops"), _doc("JobTemplate", "Deploy")]
    suite = _suite("deploy", {"c": {}})

    with pytest.raises(ConfigError, match="2 specs match JobTemplate 'Deploy'") as info:
        plan_temporary_set(
            select_cases([suite], None),
            [("a.yml", specs[0]), ("b.yml", specs[1])],
            marker=MARKER,
            sha=SHA,
            default_scope=None,
            kinds=AwxResourceCatalog().get,
        )

    assert (info.value.category, info.value.system) == ("invalid", "awx.suite")


def test_a_workflow_copy_runs_the_copies_of_the_templates_it_names() -> None:
    workflow = _doc(
        "WorkflowJobTemplate",
        "Release",
        nodes=[
            {"id": "deploy", "run": {"job_template": "Deploy"}, "success": ["verify"]},
            {"id": "verify", "run": {"job_template": "Smoke"}},
        ],
    )
    deploy = _doc("JobTemplate", "Deploy", project="Playbooks")
    suite = _suite(
        "release",
        {"c": {"launch": {"limit": "web"}}},
        jobTemplate=None,
        workflowTemplate="Release",
    )

    plan = _plan([suite], [workflow, deploy])

    assert [(copy.kind, copy.name, copy.prompts) for copy in plan.templates] == [
        ("JobTemplate", "Deploy [untaped-test 1a2b3c4 k3x9]", ()),
        ("WorkflowJobTemplate", "Release [untaped-test 1a2b3c4 k3x9]", ("ask_limit_on_launch",)),
    ]
    nodes = plan.templates[1].document.spec["nodes"]
    assert nodes == [
        {
            "id": "deploy",
            "run": {"job_template": "Deploy [untaped-test 1a2b3c4 k3x9]"},
            "success": ["verify"],
        },
        {"id": "verify", "run": {"job_template": "Smoke"}},
    ]
    assert plan.templates[1].document.spec["scm_branch"] == SHA
    assert list(plan.bindings) == ["release"]


def test_a_workflow_spec_with_invalid_nodes_names_its_file() -> None:
    workflow = _doc("WorkflowJobTemplate", "Release", nodes=[{"id": "a"}])
    suite = _suite("release", {"c": {}}, jobTemplate=None, workflowTemplate="Release")

    with pytest.raises(ConfigError, match=r"feature/x:.untaped/awx/Release.yml: .*exactly one"):
        _plan([suite], [workflow])


def test_select_cases_refuses_a_filter_that_matches_nothing() -> None:
    with pytest.raises(ConfigError, match="no case matched --case 'nope'"):
        select_cases([_suite("s", {"c": {}})], {"nope"})


def test_select_cases_keeps_declaration_order() -> None:
    suites = [_suite("a", {"x": {}, "y": {}}), _suite("b", {"x": {}})]

    assert [(suite.name, name) for suite, name, _ in select_cases(suites, {"x"})] == [
        ("a", "x"),
        ("b", "x"),
    ]
    assert isinstance(select_cases(suites, None)[0][2], Case)
