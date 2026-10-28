"""The awx rules, one by one: each fires where it should, and only there.

Documents come through ``untaped_awx.testing.resources``, the loader a real
run uses; a controller-origin case relabels that snapshot as the controller
would have read it (origin, location, facts).
"""

from __future__ import annotations

from typing import Any

import pytest

from awx_lint_support import CLEAN, DOCUMENTS
from awx_test_support import run
from untaped.sdk.marks import Experimental, function_mark
from untaped_awx.api import (
    ControllerFacts,
    Resources,
    branch_override_ignored,
    empty_inventory,
    ignored_launch_field,
    invalid_document,
    missing_reference,
    plaintext_secret,
    playbook_missing,
    survey_default_missing,
    unknown_field,
    unresolved_reference,
)
from untaped_awx.domain.lint.fixes import PATCH_GROUPS
from untaped_awx.domain.lint.rules import RULES
from untaped_awx.infrastructure.resources.catalog import AwxResourceCatalog
from untaped_awx.testing import resources
from untaped_lint.api import Finding, ResourceLocation, RuleFunction
from untaped_lint.testing import run_rule

_TEMPLATE = """\
kind: JobTemplate
metadata: {name: deploy-web, organization: Platform}
spec:
  project: web
  playbook: deploy.yml
%s
"""


def _run(rule: RuleFunction, snapshot: Resources) -> list[Finding]:
    """``rule``'s findings on ``snapshot``, checking it left the snapshot as it was."""
    before = snapshot.model_dump(mode="json")
    found = run_rule(rule, snapshot)
    assert snapshot.model_dump(mode="json") == before
    return found


def _messages(rule: RuleFunction, snapshot: Resources) -> list[str]:
    return [each.message for each in _run(rule, snapshot)]


def _live(snapshot: Resources, **facts: ControllerFacts) -> Resources:
    """``snapshot`` as the controller origin would hold it; ``facts`` by resource name."""
    items = tuple(
        item.model_copy(
            update={
                "origin": "controller",
                "location": ResourceLocation(id=f"things/{index}"),
                "controller": facts.get(item.resource.metadata.name, ControllerFacts()),
            }
        )
        for index, item in enumerate(snapshot.items, start=1)
    )
    return snapshot.model_copy(update={"items": items, "online": True})


def test_rules_are_listed_once_each_in_table_order() -> None:
    ids = [each.rule.id for each in RULES]
    assert ids == [
        "missing-reference",
        "unresolved-reference",
        "ignored-launch-field",
        "branch-override-ignored",
        "survey-default-missing",
        "unknown-field",
        "playbook-missing",
        "empty-inventory",
        "plaintext-secret",
        "invalid-document",
    ]


@pytest.mark.parametrize("function", RULES, ids=lambda each: each.rule.id)
def test_each_rule_is_the_constant_its_module_names_and_experimental(
    function: RuleFunction,
) -> None:
    assert function.rule.id == function.__name__.replace("_", "-")
    assert function.__module__.rsplit(".", 1)[-1] == function.__name__
    assert isinstance(function_mark(function), Experimental)


@pytest.mark.parametrize("function", RULES, ids=lambda each: each.rule.id)
def test_no_rule_fires_on_a_clean_batch(function: RuleFunction) -> None:
    assert _run(function, resources(CLEAN)) == []


def test_patch_groups_name_the_catalog_groups_whose_patch_sets_a_field() -> None:
    catalog = AwxResourceCatalog()
    for kind, group in PATCH_GROUPS.items():
        spec = catalog.get(kind)
        assert spec.cli_name == group
        assert "apply" in spec.commands  # the commands that come with ``patch``


# --- missing-reference -------------------------------------------------------


def test_missing_reference_names_the_batch_offline() -> None:
    snapshot = resources(
        _TEMPLATE % "", "kind: Project\nmetadata: {name: api, organization: Platform}\n"
    )
    found = _run(missing_reference, snapshot)
    assert [each.message for each in found] == ["project 'web' is not in the batch"]
    assert found[0].target == "JobTemplate/Platform/deploy-web"
    assert found[0].hint == "add a Project document named 'web' in Platform, or fix the name"


def test_missing_reference_reports_a_required_reference_written_as_null() -> None:
    text = _TEMPLATE.replace("project: web", "project: null")
    snapshot = resources(text % "  inventory: null\n  ask_inventory_on_launch: false")
    assert _messages(missing_reference, snapshot) == ["project is empty", "inventory is empty"]


@pytest.mark.parametrize("flag", ["\n  ask_inventory_on_launch: true", ""])
def test_an_inventory_asked_on_launch_or_maybe_asked_may_be_empty(flag: str) -> None:
    text = _TEMPLATE % f"  inventory: null{flag}"  # a partial document may leave the flag out
    snapshot = resources(text, "kind: Project\nmetadata: {name: web, organization: Platform}\n")
    assert _messages(missing_reference, snapshot) == []


def test_missing_reference_names_the_controller_on_the_live_origin() -> None:
    snapshot = _live(resources(_TEMPLATE % "", "kind: Project\nmetadata: {name: api}\n"))
    found = _run(missing_reference, snapshot)
    assert [each.message for each in found] == ["project 'web' is not in the controller"]
    assert found[0].hint == "create Project 'web' in Platform, or change project"


# --- unresolved-reference ----------------------------------------------------


def test_unresolved_reference_names_the_kind_nothing_documents() -> None:
    assert _messages(unresolved_reference, resources(_TEMPLATE % "")) == [
        "project 'web' was not checked: no Project documents, offline"
    ]


def test_an_id_offline_is_unresolved_for_being_an_id() -> None:
    """N3: the batch holds Project documents; the id is what cannot be checked offline."""
    text = _TEMPLATE.replace("project: web", "project: 14") % ""
    snapshot = resources(text, "kind: Project\nmetadata: {name: web, organization: Platform}\n")
    (found,) = _run(unresolved_reference, snapshot)
    assert found.message == (
        "project '14' was not checked: an id is checked on the controller only, offline"
    )
    assert found.hint == "lint without --offline to check it on the controller"


def test_a_read_only_kind_is_never_offered_as_a_document_to_add() -> None:
    text = _TEMPLATE % "  credentials: [ssh]"
    found = {each.message: each.hint for each in _run(unresolved_reference, resources(text))}
    assert found["credentials 'ssh' was not checked: no Credential documents, offline"] == (
        "lint without --offline to check it on the controller"
    )
    assert found["project 'web' was not checked: no Project documents, offline"] == (
        "lint without --offline to check it on the controller, or add its document"
    )


def test_a_reference_the_token_cannot_read_says_which_reason() -> None:
    from untaped_awx.api import Reference
    from untaped_awx.domain import IdentityRef

    snapshot = _live(resources(_TEMPLATE % ""))
    unread = Reference(
        field="project", target=IdentityRef(kind="Project", name="#10"), resolution="unresolved"
    )
    item = snapshot.items[0].model_copy(update={"references": (unread,)})
    for loaded, why in (
        ((), "the token cannot list Project records"),
        (("Project",), "the token cannot read that Project"),
    ):
        live = snapshot.model_copy(update={"items": (item,), "loaded": loaded})
        assert _messages(unresolved_reference, live) == [f"project '#10' was not checked: {why}"]


def test_a_missing_name_of_a_documented_kind_is_not_unresolved() -> None:
    snapshot = resources(_TEMPLATE % "", "kind: Project\nmetadata: {name: api}\n")
    assert _run(unresolved_reference, snapshot) == []


# --- ignored-launch-field ----------------------------------------------------


def test_ignored_launch_field_reports_a_schedule_field_the_template_does_not_ask_for() -> None:
    found = _run(ignored_launch_field, resources(DOCUMENTS))
    assert [(each.target, each.message) for each in found] == [
        (
            "Schedule/Platform/nightly",
            "limit is set, but JobTemplate 'deploy-web' ignores it: ask_limit_on_launch is false",
        )
    ]
    assert found[0].fix is None  # a document is fixed in the document


def test_ignored_launch_field_offers_the_patch_on_the_controller() -> None:
    found = _run(ignored_launch_field, _live(resources(DOCUMENTS)))
    assert [each.fix for each in found] == [
        [
            "awx", "job-templates", "patch", "deploy-web", "--organization", "Platform",
            "--set", "ask_limit_on_launch=true",
        ]
    ]  # fmt: skip


def test_a_flag_the_template_leaves_out_is_not_judged() -> None:
    snapshot = resources(DOCUMENTS.replace("  ask_limit_on_launch: false\n", ""))
    assert _run(ignored_launch_field, snapshot) == []


def test_survey_variables_pass_while_other_extra_data_is_ignored() -> None:
    template = _TEMPLATE % (
        "  ask_variables_on_launch: false\n  survey_enabled: true\n"
        "  survey_spec: {spec: [{variable: region, required: false}]}"
    )
    schedule = (
        "kind: Schedule\nmetadata:\n  name: nightly\n"
        "  parent: {kind: JobTemplate, name: deploy-web, organization: Platform}\n"
        "spec: {extra_data: {region: eu, debug: true}}\n"
    )
    assert _messages(ignored_launch_field, resources(template, schedule)) == [
        "extra_data sets debug, which JobTemplate 'deploy-web' ignores: only its survey's "
        "variables are accepted while ask_variables_on_launch is false"
    ]


def test_a_workflow_node_prompt_is_judged_against_the_template_it_runs() -> None:
    workflow = """\
kind: WorkflowJobTemplate
metadata: {name: release, organization: Platform}
spec:
  nodes:
    - id: deploy
      run: {job_template: deploy-web}
      prompts: {limit: web1}
"""
    snapshot = resources(_TEMPLATE % "  ask_limit_on_launch: false", workflow)
    assert _messages(ignored_launch_field, snapshot) == [
        "nodes[deploy].prompts.limit is set, but JobTemplate 'deploy-web' ignores it: "
        "ask_limit_on_launch is false"
    ]


# --- branch-override-ignored -------------------------------------------------


def test_branch_override_ignored_names_the_branch_jobs_run() -> None:
    found = _run(branch_override_ignored, resources(DOCUMENTS))
    assert [each.message for each in found] == [
        "scm_branch 'feature' is ignored: Project 'web' does not allow the override, "
        "so jobs run its branch 'main'"
    ]
    assert _run(branch_override_ignored, _live(resources(DOCUMENTS)))[0].fix == [
        "awx", "projects", "patch", "web", "--organization", "Platform",
        "--set", "allow_override=true",
    ]  # fmt: skip


def test_a_project_that_allows_the_override_is_not_reported() -> None:
    snapshot = resources(DOCUMENTS.replace("allow_override: false", "allow_override: true"))
    assert _run(branch_override_ignored, snapshot) == []


# --- survey-default-missing --------------------------------------------------


def test_survey_default_missing_reports_an_unanswered_required_question() -> None:
    assert _messages(survey_default_missing, resources(DOCUMENTS)) == [
        "required survey question 'region' of JobTemplate 'deploy-web' has no default, "
        "and extra_data does not answer it"
    ]


def test_a_schedule_that_answers_the_question_is_not_reported() -> None:
    text = DOCUMENTS.replace("  limit: web1\n", "  extra_data: {region: eu}\n")
    assert _run(survey_default_missing, resources(text)) == []


def test_a_disabled_survey_asks_nothing() -> None:
    text = DOCUMENTS.replace("survey_enabled: true", "survey_enabled: false")
    assert _run(survey_default_missing, resources(text)) == []


# --- unknown-field -----------------------------------------------------------


def test_unknown_field_lists_every_unknown_field_once_per_document() -> None:
    snapshot = resources(_TEMPLATE % "  playbok: x.yml\n  forkz: 5")
    assert _messages(unknown_field, snapshot) == [
        "2 fields untaped does not know for JobTemplate: forkz, playbok"
    ]
    assert _run(unknown_field, _live(snapshot)) == []


# --- playbook-missing and empty-inventory (controller facts) -----------------


def test_playbook_missing_reads_the_project_at_its_last_successful_sync() -> None:
    synced = ControllerFacts(status="successful", playbooks=("site.yml",))
    snapshot = _live(resources(CLEAN), web=synced)
    assert _messages(playbook_missing, snapshot) == [
        "playbook 'deploy.yml' is not in Project 'web' at its last sync"
    ]
    failed = ControllerFacts(status="failed", playbooks=("site.yml",))
    assert _run(playbook_missing, _live(resources(CLEAN), web=failed)) == []
    assert _run(playbook_missing, resources(CLEAN)) == []


def test_empty_inventory_reads_the_host_count() -> None:
    assert _messages(empty_inventory, _live(resources(CLEAN), prod=ControllerFacts(total_hosts=0)))
    assert _run(empty_inventory, _live(resources(CLEAN), prod=ControllerFacts(total_hosts=3))) == []


# --- plaintext-secret --------------------------------------------------------


def test_plaintext_secret_reports_secret_paths_and_secret_named_variables() -> None:
    text = _TEMPLATE % (
        '  extra_vars: \'{"db_password": "hunter2", "api_token": "{{ vault_token }}"}\'\n'
        "  survey_spec:\n    spec: [{variable: pw, type: password, default: hunter2}]"
    )
    messages = _messages(plaintext_secret, resources(text))
    assert messages == [
        "survey_spec.spec.*[type=password].default holds a secret in plain text",
        "extra_vars: db_password looks like a secret in plain text",
    ]


def test_a_credential_input_counts_only_under_a_secret_name() -> None:
    """S4: ``inputs.*`` holds a username too; only the secret-named input is reported."""
    text = (
        "kind: Credential\nmetadata: {name: ssh, organization: Platform}\n"
        "spec: {inputs: {username: deploy, password: hunter2, ssh_key_data: '-----BEGIN'}}\n"
    )
    assert _messages(plaintext_secret, resources(text)) == [
        "inputs.password holds a secret in plain text",
        "inputs.ssh_key_data holds a secret in plain text",
    ]


@pytest.mark.parametrize(
    ("document", "field"),
    [
        ("kind: Inventory\nmetadata: {name: prod, organization: Platform}\nspec:\n", "variables"),
        ("kind: Host\nmetadata:\n  name: web1\n  parent: {kind: Inventory, name: prod}\n"
         "spec:\n", "variables"),
        ("kind: Group\nmetadata:\n  name: web\n  parent: {kind: Inventory, name: prod}\n"
         "spec:\n", "variables"),
        ("kind: Inventory\nmetadata: {name: prod, organization: Platform}\nspec:\n",
         "source_vars"),
    ],
    ids=["inventory", "host", "group", "source-vars"],
)  # fmt: skip
def test_inventory_variables_are_read_as_the_snapshot_redacts_them(
    document: str, field: str
) -> None:
    """S5: the fields redaction hides are the fields the rule reads."""
    from untaped_awx.domain.lint.secrets import VARIABLES, redact_variables

    snapshot = resources(document + f"  {field}: 'ansible_password: swordfish'\n")
    (item,) = snapshot.items
    assert field in VARIABLES[item.resource.kind]
    assert _messages(plaintext_secret, snapshot) == [
        f"{field}: ansible_password looks like a secret in plain text"
    ]
    assert "swordfish" not in str(redact_variables(item.resource).spec)


def test_a_workflow_node_prompt_secret_is_reported_and_redacted() -> None:
    from untaped_awx.domain.lint.secrets import redact_variables

    text = (
        "kind: WorkflowJobTemplate\nmetadata: {name: release, organization: Platform}\n"
        "spec:\n  nodes:\n    - id: one\n      run: {job_template: deploy}\n"
        "      prompts: {extra_vars: {vault_password: opensesame}}\n"
    )
    snapshot = resources(text)
    assert _messages(plaintext_secret, snapshot) == [
        "nodes[one].prompts.extra_vars: vault_password looks like a secret in plain text"
    ]
    assert "opensesame" not in str(redact_variables(snapshot.items[0].resource).spec)


def test_encrypted_and_templated_values_are_not_plaintext() -> None:
    text = _TEMPLATE % "  extra_vars: {db_password: $encrypted$, token: '{{ t }}', secret: ''}"
    assert _run(plaintext_secret, resources(text)) == []


# --- invalid-document --------------------------------------------------------


def test_invalid_document_reports_each_unreadable_document_by_its_index() -> None:
    text = "kind: Nope\nmetadata: {name: x}\n---\n---\n[1, 2]\n---\nkind: JobTemplate\n"
    found = _run(invalid_document, resources(text))
    assert [(each.target, each.location) for each in found] == [
        ("text1.yml", found[0].location),
        ("text1.yml", found[1].location),
        ("text1.yml", found[2].location),
    ]
    assert [getattr(each.location, "index", None) for each in found] == [1, 3, 4]
    assert found[0].message == "unknown kind 'Nope'"
    assert found[0].hint is not None and found[0].hint.startswith("available: Credential")
    assert found[1].message == "each YAML doc must be a mapping"
    assert not found[2].message.startswith("text1.yml")


def test_an_invalid_document_message_is_one_line_and_the_detail_its_hint() -> None:
    """N7: a validation error's lines become one message and a one-line hint."""
    text = "kind: JobTemplate\nmetadata: {name: 3, bogus: 1}\nspec: []\n---\nkey: [\n"
    found = _run(invalid_document, resources(text))
    assert [each.message for each in found] == [
        "not a valid resource: metadata.name: Input should be a valid string",
        "invalid YAML: while parsing a flow node",
    ]
    assert found[0].hint == (
        "metadata.bogus: Extra inputs are not permitted; spec: Input should be a valid dictionary"
    )
    assert all("\n" not in (each.hint or "") for each in found)


# --- fixes parse ---------------------------------------------------------------


def test_every_fix_is_a_command_the_awx_app_parses(fake_aap: Any) -> None:
    found = [
        *_run(ignored_launch_field, _live(resources(DOCUMENTS))),
        *_run(branch_override_ignored, _live(resources(DOCUMENTS))),
    ]
    fixes = [each.fix for each in found if each.fix]
    assert len(fixes) == 2
    for fix in fixes:
        assert fix[0] == "awx"
        assert fix[2] == "patch", fix
        # parsed, it runs: the empty controller has no such resource (exit 1, not a usage error)
        result = run([*fix[1:], "--dry-run"])
        assert result.exit_code == 1, result.stderr
