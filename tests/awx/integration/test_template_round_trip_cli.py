"""Export → apply under a new name reproduces a template's configuration."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration

_SURVEY = {
    "name": "Deploy survey",
    "description": "",
    "spec": [
        {
            "variable": "region",
            "question_name": "Region",
            "type": "multiplechoice",
            "choices": ["eu", "us"],
            "default": "eu",
            "required": True,
        },
        {
            "variable": "replicas",
            "question_name": "Replicas",
            "type": "integer",
            "default": 2,
            "required": False,
        },
        {
            "variable": "api_token",
            "question_name": "API token",
            "type": "password",
            "default": "s3cret",
            "required": False,
        },
    ],
}


def _seed(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed("projects", id=10, name="playbooks", organization=1)
    fake.seed("inventories", id=20, name="Production", organization=1, kind="")
    fake.seed("credentials", id=40, name="ssh", organization=1, credential_type=1)
    fake.seed("credentials", id=41, name="vault", organization=1, credential_type=3)
    fake.seed("labels", id=50, name="web", organization=1)
    fake.seed("labels", id=51, name="nightly", organization=1)
    fake.seed(
        "job_templates",
        id=30,
        name="Deploy",
        organization=1,
        project=10,
        inventory=20,
        playbook="deploy.yml",
        description="Deploy the app",
        extra_vars='{"region": "eu", "retries": 3}',
        survey_enabled=True,
        survey_spec=copy.deepcopy(_SURVEY),
    )
    fake.memberships[("job_templates", 30, "credentials")] = {40, 41}
    fake.memberships[("job_templates", 30, "labels")] = {50, 51}


def _export(name: str) -> dict[str, Any]:
    result = CliInvoker().invoke(
        app, ["job-templates", "export", name, "--organization", "Default"]
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    document = yaml.safe_load(result.stdout)
    assert isinstance(document, dict)
    return document


def _without_secret_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    stripped = copy.deepcopy(spec)
    for question in stripped["survey_spec"]["spec"]:
        if question["type"] == "password":
            question.pop("default", None)
    return stripped


def _apply(path: Path) -> None:
    result = CliInvoker().invoke(app, ["job-templates", "apply", str(path), "--yes"])
    assert result.exit_code == 0, result.output + (result.stderr or "")


def _find(fake: Any, name: str) -> dict[str, Any]:
    return next(r for r in fake.list_records("job_templates") if r["name"] == name)


def test_export_apply_under_new_name_reproduces_non_secret_configuration(
    fake_aap: Any, tmp_path: Path
) -> None:
    _seed(fake_aap)
    source = _export("Deploy")

    # Password defaults leave as a placeholder; plain defaults stay readable.
    questions = {q["variable"]: q for q in source["spec"]["survey_spec"]["spec"]}
    assert questions["region"]["default"] == "eu"
    assert questions["replicas"]["default"] == 2
    assert questions["api_token"]["default"] == "$encrypted$"
    assert sorted(source["spec"]["labels"]) == ["nightly", "web"]
    assert sorted(source["spec"]["credentials"]) == ["ssh", "vault"]

    renamed = copy.deepcopy(source)
    renamed["metadata"]["name"] = "Deploy next"
    document = tmp_path / "deploy-next.yml"
    document.write_text(yaml.safe_dump(renamed, sort_keys=False))
    _apply(document)

    copied = _find(fake_aap, "Deploy next")
    stored = {q["variable"]: q for q in copied["survey_spec"]["spec"]}
    assert "default" not in stored["api_token"]
    assert stored["region"]["default"] == "eu"
    assert fake_aap.memberships[("job_templates", copied["id"], "credentials")] == {40, 41}
    assert fake_aap.memberships[("job_templates", copied["id"], "labels")] == {50, 51}

    target = _export("Deploy next")
    assert target["kind"] == source["kind"]
    assert target["metadata"] == {**source["metadata"], "name": "Deploy next"}
    for spec in (source["spec"], target["spec"]):
        spec["labels"].sort()
        spec["credentials"].sort()
    assert _without_secret_defaults(target["spec"]) == _without_secret_defaults(source["spec"])
    assert target["spec"]["extra_vars"] == source["spec"]["extra_vars"]


def test_reapplying_an_export_leaves_the_template_unchanged(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    document = tmp_path / "deploy.yml"
    document.write_text(yaml.safe_dump(_export("Deploy"), sort_keys=False))

    result = CliInvoker().invoke(
        app, ["job-templates", "apply", str(document), "--dry-run", "--format", "json"]
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert '"unchanged"' in result.stdout
    # The stored password default is kept, not wiped by the placeholder.
    stored = _find(fake_aap, "Deploy")["survey_spec"]["spec"][2]
    assert stored["default"] == "s3cret"


def test_apply_replaces_labels_adding_before_removing(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    fake_aap.seed("labels", id=52, name="canary", organization=1)
    document = tmp_path / "deploy.yml"
    document.write_text(
        "kind: JobTemplate\n"
        "metadata: {name: Deploy, organization: Default}\n"
        "spec: {labels: [web, canary]}\n"
    )

    _apply(document)

    assert fake_aap.memberships[("job_templates", 30, "labels")] == {50, 52}
    label_posts = [
        call.request.content
        for call in fake_aap.router.calls
        if call.request.method == "POST" and call.request.url.path.endswith("/labels/")
    ]
    assert label_posts == [b'{"id":52}', b'{"id":51,"disassociate":true}']


def test_apply_refuses_unknown_label_before_any_write(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    document = tmp_path / "deploy.yml"
    document.write_text(
        "kind: JobTemplate\n"
        "metadata: {name: Deploy, organization: Default}\n"
        "spec: {description: changed, labels: [web, missing]}\n"
    )

    result = CliInvoker().invoke(app, ["job-templates", "apply", str(document), "--yes"])

    assert result.exit_code == 1
    assert "missing" in result.output + (result.stderr or "")
    assert not [call for call in fake_aap.router.calls if call.request.method != "GET"]
    assert fake_aap.get_record("job_templates", 30)["description"] == "Deploy the app"


def test_apply_clears_a_survey_through_its_endpoint(fake_aap: Any, tmp_path: Path) -> None:
    _seed(fake_aap)
    document = tmp_path / "deploy.yml"
    document.write_text(
        "kind: JobTemplate\n"
        "metadata: {name: Deploy, organization: Default}\n"
        "spec: {survey_enabled: false, survey_spec: {}}\n"
    )

    _apply(document)

    record = fake_aap.get_record("job_templates", 30)
    assert record["survey_spec"] == {}
    assert record["survey_enabled"] is False
    assert any(
        call.request.method == "DELETE"
        and call.request.url.path.endswith("/job_templates/30/survey_spec/")
        for call in fake_aap.router.calls
    )
