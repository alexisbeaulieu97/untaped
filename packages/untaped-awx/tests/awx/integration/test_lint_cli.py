"""``untaped awx lint``: the three origins, end to end against the fake controller."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from awx.lint_support import CLEAN, DOCUMENTS, seed_controller
from untaped.sdk import plugin_dir
from untaped.testing import CliResult, compose_with, damage, invoke_cli
from untaped_awx import SPEC
from untaped_awx.api import AwxResources
from untaped_awx.cli import app
from untaped_awx.cli.context import AwxContext
from untaped_lint.api import Finding, ResourceLocation, Rules, rule

pytestmark = pytest.mark.usefixtures("fresh_composition")

_PLATFORM_SUMMARY = {"organization": {"id": 1, "name": "Platform"}}


@pytest.fixture(autouse=True)
def _outside_this_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run where no ``.untaped/lint.yml`` is found: this repository's would add its ignores."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def composed(fresh_composition: None) -> Iterator[None]:
    with compose_with("awx", "lint"):
        yield


def _lint(*args: str) -> CliResult:
    return invoke_cli(app, ["lint", "-f", "json", *args])


def _rows(result: CliResult) -> list[dict[str, Any]]:
    assert result.stdout.strip(), result.output
    return json.loads(result.stdout)


def _found(result: CliResult) -> list[tuple[str, str]]:
    return sorted((row["rule"], row["target"]) for row in _rows(result))


def _requests(fake: Any) -> list[str]:
    return [f"{call.request.method} {call.request.url.path}" for call in fake.router.calls]


def _files(tmp_path: Path, **texts: str) -> Path:
    folder = tmp_path / "specs"
    folder.mkdir()
    for name, text in texts.items():
        (folder / f"{name}.yml").write_text(text, encoding="utf-8")
    return folder


def _snapshot_files() -> list[Path]:
    """The files ``awx.caches.resources`` keeps: one per controller and token, for every profile."""
    return sorted((plugin_dir(SPEC) / "cached" / "resources").glob("*.json"))


def _stored(token: str = "secret") -> AwxResources:
    """The snapshot ``awx.caches.resources`` keeps for the fake controller and ``token``."""
    from untaped_awx.settings import RESOURCES

    served = RESOURCES.cached("https://aap.example.com", partition=token)
    assert served is not None
    return served.value


def _kept_snapshot(file: Path) -> str:
    """The snapshot a cache file holds: its second line, after the freshness stamp."""
    _stamp, value = file.read_text(encoding="utf-8").splitlines()
    return value


# --- FILES --offline --------------------------------------------------------------


@pytest.mark.usefixtures("composed")
def test_offline_documents_lint_with_no_profile_and_no_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("--offline opened the awx context")

    monkeypatch.setattr(AwxContext, "__init__", refuse)
    folder = _files(tmp_path, templates=DOCUMENTS)
    result = _lint(str(folder), "--offline")
    assert result.exit_code == 3, result.output
    assert _found(result) == [
        ("awx.branch-override-ignored", "JobTemplate/Platform/deploy-web"),
        ("awx.ignored-launch-field", "Schedule/Platform/nightly"),
        ("awx.survey-default-missing", "Schedule/Platform/nightly"),
    ]
    rows = _rows(result)
    assert rows[0]["location"] == {"path": str(folder / "templates.yml"), "index": 3}
    assert "lint: 2 errors, 1 warning" in result.stderr


@pytest.mark.usefixtures("composed")
def test_a_clean_batch_exits_0(tmp_path: Path) -> None:
    result = _lint(str(_files(tmp_path, clean=CLEAN)), "--offline")
    assert result.exit_code == 0, result.output
    assert _rows(result) == []


@pytest.mark.usefixtures("composed")
def test_ignore_and_strict_are_lints_own_spellings(tmp_path: Path) -> None:
    folder = str(_files(tmp_path, templates=DOCUMENTS))
    result = _lint(folder, "--offline", "--ignore", "awx.branch-override-ignored")
    assert "awx.branch-override-ignored" not in {row for row, _ in _found(result)}
    only_warning = [
        "--ignore", "awx.branch-override-ignored", "--ignore", "awx.ignored-launch-field",
    ]  # fmt: skip
    assert _lint(folder, "--offline", *only_warning).exit_code == 0
    assert _lint(folder, "--offline", *only_warning, "--strict").exit_code == 3


@pytest.mark.usefixtures("composed")
def test_narrowing_never_hides_a_document_it_could_not_read(tmp_path: Path) -> None:
    """S3: an unreadable document has no kind to narrow by; it is reported anyway."""
    folder = _files(tmp_path, clean=CLEAN, broken="kind: JobTemplate\nmetadata: [\n")
    for narrowing in (["--kind", "projects"], ["--org", "Finance"]):
        result = _lint(str(folder), "--offline", *narrowing)
        assert result.exit_code == 3, result.output
        assert _found(result) == [("awx.invalid-document", str(folder / "broken.yml"))]


@pytest.mark.usefixtures("composed")
def test_kind_and_organization_narrow_what_is_reported(tmp_path: Path) -> None:
    folder = str(_files(tmp_path, templates=DOCUMENTS))
    assert _found(_lint(folder, "--offline", "--kind", "job-templates")) == [
        ("awx.branch-override-ignored", "JobTemplate/Platform/deploy-web")
    ]
    assert [rule for rule, _ in _found(_lint(folder, "--offline", "--kind", "Schedule"))] == [
        "awx.ignored-launch-field",
        "awx.survey-default-missing",
    ]
    assert len(_found(_lint(folder, "--offline", "--org", "Platform"))) == 3
    assert _found(_lint(folder, "--offline", "--org", "Finance")) == []


# --- FILES online -------------------------------------------------------------------


@pytest.mark.usefixtures("composed")
def test_online_a_reference_the_batch_lacks_is_looked_up_on_the_controller(
    fake_aap: Any, tmp_path: Path
) -> None:
    fake_aap.seed("organizations", id=1, name="Platform")
    fake_aap.seed("inventories", id=20, name="prod", organization=1, kind="")
    template = (
        "kind: JobTemplate\nmetadata: {name: deploy, organization: Platform}\n"
        "spec: {project: web, inventory: prod, playbook: site.yml}\n"
    )
    result = _lint(str(_files(tmp_path, template=template)))
    assert result.exit_code == 3, result.output
    assert [row["message"] for row in _rows(result)] == [
        "project 'web' is not in the batch or on the controller"
    ]


# --- the controller -----------------------------------------------------------------


@pytest.mark.usefixtures("composed")
def test_the_controller_origin_reads_every_kind_once_and_reports_each_rule(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    result = _lint()
    assert result.exit_code == 3, result.output
    assert _found(result) == [
        ("awx.branch-override-ignored", "JobTemplate/Platform/deploy-web"),
        ("awx.empty-inventory", "Inventory/Platform/prod"),
        ("awx.ignored-launch-field", "Schedule/Platform/nightly"),
        ("awx.missing-reference", "JobTemplate/Platform/orphan"),
        ("awx.missing-reference", "WorkflowJobTemplate/Platform/release"),
        ("awx.playbook-missing", "JobTemplate/Platform/deploy-web"),
    ]
    rows = {row["rule"]: row for row in _rows(result)}
    assert rows["awx.playbook-missing"]["location"] == {"id": "job_templates/100", "url": None}
    assert rows["awx.ignored-launch-field"]["fix"] == [
        "awx", "job-templates", "patch", "deploy-web", "--organization", "Platform",
        "--set", "ask_limit_on_launch=true",
    ]  # fmt: skip
    listed = [request for request in _requests(fake_aap) if request.count("/") == 4]
    assert len(listed) == len(set(listed)) == 15  # one list per catalog kind


@pytest.mark.usefixtures("composed")
def test_the_snapshot_is_redacted_owner_only_and_served_from_the_cache(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    first = _lint()
    [snapshot] = _snapshot_files()
    text = _kept_snapshot(snapshot)
    assert "hunter2" not in text  # a template's launch variable
    assert "opensesame" not in text  # a workflow node's prompts.extra_vars
    assert "swordfish" not in text  # an inventory's variable
    assert "ansible_user" in text  # what is not secret-named stays
    assert "$encrypted$" in text
    assert snapshot.stat().st_mode & 0o077 == 0
    AwxResources.model_validate_json(text)
    before = len(fake_aap.router.calls)
    second = _lint()
    assert len(fake_aap.router.calls) == before  # served: nothing asked of the controller
    assert _rows(second) == _rows(first)
    _lint("--refresh")
    assert len(fake_aap.router.calls) > before


@pytest.mark.usefixtures("composed")
def test_a_snapshot_file_gone_is_read_again(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    _lint()
    [snapshot] = _snapshot_files()
    snapshot.unlink()
    before = len(fake_aap.router.calls)
    result = _lint()
    assert result.exit_code == 3, result.output
    assert len(fake_aap.router.calls) > before
    assert _snapshot_files() == [snapshot]


@pytest.mark.usefixtures("composed")
def test_a_damaged_snapshot_is_read_again(fake_aap: Any) -> None:
    from untaped_awx.settings import RESOURCES

    seed_controller(fake_aap)
    _lint()
    damage(RESOURCES, "https://aap.example.com", partition="secret")
    before = len(fake_aap.router.calls)
    result = _lint()
    assert result.exit_code == 3, result.output
    assert len(fake_aap.router.calls) > before
    [snapshot] = _snapshot_files()
    AwxResources.model_validate_json(_kept_snapshot(snapshot))


@pytest.mark.usefixtures("composed")
def test_cached_with_nothing_kept_fails_without_asking(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    result = _lint("--cached")
    assert result.exit_code == 1, result.output
    assert _requests(fake_aap) == []
    assert "controller resources" in result.stderr


@pytest.mark.usefixtures("composed")
def test_a_code_version_change_reads_the_controller_again(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S22: a snapshot another version of the loader filled is read again."""
    from untaped_awx.settings import RESOURCES

    seed_controller(fake_aap)
    _lint()
    before = len(fake_aap.router.calls)
    _lint()
    assert len(fake_aap.router.calls) == before
    monkeypatch.setattr(RESOURCES, "version", lambda: "another")
    _lint()
    assert len(fake_aap.router.calls) > before


@pytest.mark.usefixtures("composed")
def test_another_token_never_reads_this_tokens_snapshot(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N1: the token is the cache's partition, so a changed token reads the controller again."""
    first, second = "tok-1f2e3d", "tok-9a8b7c"
    monkeypatch.setenv("UNTAPED_AWX__TOKEN", first)
    seed_controller(fake_aap)
    _lint()
    before = len(fake_aap.router.calls)
    monkeypatch.setenv("UNTAPED_AWX__TOKEN", second)
    result = _lint()
    assert result.exit_code == 3, result.output
    assert len(fake_aap.router.calls) > before
    files = _snapshot_files()
    assert len(files) == 2  # one per token
    for file in files:
        assert first not in file.name and second not in file.name
        assert first not in file.read_text(encoding="utf-8")
        assert second not in file.read_text(encoding="utf-8")
    assert _stored(first) is not None and _stored(second) is not None


@pytest.mark.usefixtures("composed")
def test_two_profiles_on_one_controller_and_token_share_its_snapshot(
    fake_aap: Any, aap_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The profile is not part of the snapshot's identity: the controller and token are."""
    aap_config.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              api_prefix: /api/v2/
          other:
            awx:
              base_url: https://aap.example.com
              api_prefix: /api/v2/
        """
    )
    seed_controller(fake_aap)
    _lint()
    before = len(fake_aap.router.calls)
    monkeypatch.setenv("UNTAPED_PROFILE", "other")
    result = _lint()
    assert result.exit_code == 3, result.output
    assert len(fake_aap.router.calls) == before  # served the default profile's snapshot
    assert len(_snapshot_files()) == 1


@pytest.mark.usefixtures("composed")
def test_a_kind_the_token_cannot_list_is_unresolved_not_absent(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    fake_aap.list_errors["projects"] = 403
    result = _lint()
    assert "the token cannot list Project" in result.stderr
    found = _found(result)
    assert ("awx.unresolved-reference", "JobTemplate/Platform/deploy-web") in found
    assert ("awx.playbook-missing", "JobTemplate/Platform/deploy-web") not in found
    searched = _lint("--search", "deploy")  # lists only the kinds the token could list
    assert ("awx.unresolved-reference", "JobTemplate/Platform/deploy-web") in _found(searched)


@pytest.mark.usefixtures("composed")
def test_a_record_the_token_can_neither_list_nor_read_is_unresolved(fake_aap: Any) -> None:
    """S1: export would fail on the project's detail; lint names it ``#10`` and goes on."""
    seed_controller(fake_aap)
    fake_aap.list_errors["projects"] = 403
    fake_aap.detail_errors[("projects", 10)] = 403
    result = _lint()
    assert result.exit_code == 3, result.output
    messages = [
        row["message"] for row in _rows(result) if row["rule"] == "awx.unresolved-reference"
    ]
    assert "project '#10' was not checked: the token cannot list Project records" in messages


@pytest.mark.usefixtures("composed")
def test_a_kind_the_token_cannot_list_is_named_from_summary_fields(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    template = fake_aap.store["job_templates"][100]
    template["summary_fields"] = {
        **template["summary_fields"],
        "project": {"id": 10, "name": "web"},
    }
    fake_aap.list_errors["projects"] = 403
    fake_aap.detail_errors[("projects", 10)] = 403
    result = _lint()
    messages = [row["message"] for row in _rows(result) if row["target"].endswith("/deploy-web")]
    assert "project 'web' was not checked: the token cannot list Project records" in messages
    assert "GET /api/v2/projects/10/" not in _requests(fake_aap)


@pytest.mark.usefixtures("composed")
def test_a_hidden_record_is_named_by_its_referrers_summary_and_read_once_at_most(
    fake_aap: Any,
) -> None:
    """A project out of the token's sight, five templates on it: its name, one read at most."""
    seed_controller(fake_aap)
    fake_aap.detail_errors[("projects", 11)] = 403
    for index, summary in enumerate([{"id": 11, "name": "hidden"}] + [None] * 4):
        fields = {**_PLATFORM_SUMMARY, **({"project": summary} if summary else {})}
        fake_aap.seed(
            "job_templates", id=150 + index, name=f"t{index}", organization=1, project=11,
            inventory=20, playbook="site.yml", summary_fields=fields,
        )  # fmt: skip
    result = _lint()
    assert _requests(fake_aap).count("GET /api/v2/projects/11/") == 0  # a summary names it
    messages = {
        row["target"]: row["message"]
        for row in _rows(result)
        if row["rule"] == "awx.unresolved-reference"
    }
    assert messages["JobTemplate/Platform/t0"] == (
        "project 'hidden' was not checked: the token cannot read that Project"
    )
    assert messages["JobTemplate/Platform/t4"] == messages["JobTemplate/Platform/t0"]


@pytest.mark.usefixtures("composed")
def test_a_hidden_record_no_summary_names_is_read_once(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    fake_aap.detail_errors[("projects", 11)] = 403
    for index in range(5):
        fake_aap.seed(
            "job_templates", id=150 + index, name=f"t{index}", organization=1, project=11,
            inventory=20, playbook="site.yml", summary_fields=_PLATFORM_SUMMARY,
        )  # fmt: skip
    result = _lint()
    assert _requests(fake_aap).count("GET /api/v2/projects/11/") == 1
    assert "project '#11' was not checked: the token cannot read that Project" in {
        row["message"] for row in _rows(result)
    }


@pytest.mark.usefixtures("composed")
def test_a_schedule_parent_the_token_cannot_read_says_its_template(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    fake_aap.detail_errors[("unified_job_templates", 160)] = 403
    fake_aap.seed(
        "schedules", id=401, name="weekly", unified_job_template=160,
        rrule="DTSTART:20260101T000000Z RRULE:FREQ=WEEKLY", summary_fields={},
    )  # fmt: skip
    (row,) = [row for row in _rows(_lint()) if row["location"]["id"] == "schedules/401"]
    assert row["message"] == "parent '#160' was not checked: the token cannot read its template"
    assert row["hint"] == "lint with a token that can read the template"


@pytest.mark.usefixtures("composed")
def test_an_unlisted_kinds_summary_never_makes_an_identity(fake_aap: Any) -> None:
    """A summary carries no organization: a node's prompted inventory keeps its real one."""
    seed_controller(fake_aap)
    template = fake_aap.store["job_templates"][100]
    template["summary_fields"] = {
        **template["summary_fields"],
        "inventory": {"id": 20, "name": "prod"},
    }
    fake_aap.store["workflow_nodes"][210]["inventory"] = 20
    fake_aap.list_errors["inventories"] = 403
    _lint()
    stored = _stored()
    (release,) = [item for item in stored.items if item.resource.metadata.name == "release"]
    (node,) = [node for node in release.resource.spec["nodes"] if node["id"] == "deploy"]
    assert node["prompts"]["inventory"] == "prod"  # in the workflow's organization, not null


@pytest.mark.usefixtures("composed")
def test_an_organization_less_parent_is_its_own_not_any_namesakes(fake_aap: Any) -> None:
    """Two workflows ``release``, one in no organization: its schedule is still judged."""
    seed_controller(fake_aap)
    fake_aap.seed(
        "workflow_job_templates", id=201, name="release", organization=None,
        ask_limit_on_launch=False,
    )  # fmt: skip
    fake_aap.seed(
        "schedules", id=402, name="hourly", unified_job_template=201, limit="web1",
        rrule="DTSTART:20260101T000000Z RRULE:FREQ=HOURLY",
        summary_fields={"unified_job_template": {
            "id": 201, "name": "release", "unified_job_type": "workflow_job",
        }},
    )  # fmt: skip
    found = [row for row in _rows(_lint()) if row["location"]["id"] == "schedules/402"]
    assert [row["rule"] for row in found] == ["awx.ignored-launch-field"]


@pytest.mark.usefixtures("composed")
def test_a_management_job_schedule_is_read_as_its_system_job_template(fake_aap: Any) -> None:
    """AWX's stock cleanup schedules hang off a system job template, global and named alone."""
    seed_controller(fake_aap)
    fake_aap.seed(
        "system_job_templates", id=500, name="Cleanup Job Details", job_type="cleanup_jobs"
    )
    fake_aap.seed(
        "schedules", id=403, name="Cleanup Job Schedule", unified_job_template=500,
        rrule="DTSTART:20260101T000000Z RRULE:FREQ=WEEKLY", extra_data={"days": "120"},
        summary_fields={"unified_job_template": {
            "id": 500, "name": "Cleanup Job Details", "unified_job_type": "system_job",
        }},
    )  # fmt: skip
    result = _lint()
    assert result.exit_code == 3, result.output  # seed_controller's findings, nothing worse
    assert not [row for row in _rows(result) if row["location"]["id"] == "schedules/403"]
    stored = _stored()
    (schedule,) = [item for item in stored.items if item.location.id == "schedules/403"]  # type: ignore[union-attr]
    assert schedule.resource.metadata.parent is not None
    assert schedule.resource.metadata.parent.kind == "SystemJobTemplate"
    (parent,) = schedule.references
    assert (parent.resolution, parent.location) == (
        "resolved",
        ResourceLocation(id="system_job_templates/500"),
    )


@pytest.mark.usefixtures("composed")
def test_a_node_prompting_a_hidden_record_is_not_taken_for_its_namesake(fake_aap: Any) -> None:
    """Hidden inventory 21 is called ``prod`` like visible inventory 20: the node's is 21."""
    seed_controller(fake_aap)
    fake_aap.detail_errors[("inventories", 21)] = 403
    fake_aap.seed(
        "job_templates", id=150, name="elsewhere", organization=1, project=10, inventory=21,
        playbook="site.yml",
        summary_fields={**_PLATFORM_SUMMARY, "inventory": {"id": 21, "name": "prod"}},
    )  # fmt: skip
    fake_aap.store["workflow_nodes"][210]["inventory"] = 21
    _lint()
    stored = _stored()
    (release,) = [item for item in stored.items if item.resource.metadata.name == "release"]
    (prompted,) = [ref for ref in release.references if ref.field.endswith(".prompts.inventory")]
    assert (prompted.resolution, prompted.location) == ("unresolved", None)
    assert stored.find(prompted) is None


@pytest.mark.usefixtures("composed")
def test_a_node_running_an_organization_less_template_judges_that_template(
    fake_aap: Any,
) -> None:
    """The node runs org-less ``deploy-web``, not Platform's: its prompt is judged against it."""
    seed_controller(fake_aap)
    fake_aap.seed(
        "job_templates", id=102, name="deploy-web", organization=None, project=10, inventory=20,
        playbook="site.yml", ask_limit_on_launch=False,
    )  # fmt: skip
    node = fake_aap.store["workflow_nodes"][210]
    node.update(
        unified_job_template=102,
        limit="web1",
        summary_fields={"unified_job_template": {"id": 102, "unified_job_type": "job"}},
    )
    rows = [
        row
        for row in _rows(_lint())
        if row["rule"] == "awx.ignored-launch-field" and row["target"].startswith("Workflow")
    ]
    assert [row["message"] for row in rows] == [
        "nodes[deploy].prompts.limit is set, but JobTemplate 'deploy-web' ignores it: "
        "ask_limit_on_launch is false"
    ]
    # In no organization, the template is patched by id: its name would select Platform's too.
    (fix,) = [row["fix"] for row in rows]
    assert fix == [
        "awx", "job-templates", "patch", "102", "--by-id", "--set", "ask_limit_on_launch=true",
    ]  # fmt: skip
    applied = invoke_cli(app, [*fix[1:], "--yes"])
    assert applied.exit_code == 0, applied.output
    assert fake_aap.store["job_templates"][102]["ask_limit_on_launch"] is True
    assert fake_aap.store["job_templates"][100]["ask_limit_on_launch"] is False


def _across_organizations(fake: Any) -> None:
    """``deploy`` in Platform runs project ``web`` of Shared with credential ``mine`` of none."""
    fake.seed("organizations", id=1, name="Platform")
    fake.seed("organizations", id=2, name="Shared")
    platform = {"organization": {"id": 1, "name": "Platform"}}
    fake.seed(
        "projects", id=10, name="web", organization=2, status="successful", scm_type="git",
        scm_url="https://git.example.com/web.git", allow_override=False,
        summary_fields={"organization": {"id": 2, "name": "Shared"}},
    )  # fmt: skip
    fake.playbooks[10] = ["site.yml"]
    fake.seed(
        "inventories", id=20, name="prod", organization=1, kind="", total_hosts=2,
        summary_fields=platform,
    )  # fmt: skip
    fake.seed("credentials", id=30, name="mine", organization=None)
    fake.seed(
        "job_templates", id=100, name="deploy", organization=1, project=10, inventory=20,
        playbook="site.yml", summary_fields=platform,
    )  # fmt: skip
    fake.memberships[("job_templates", 100, "credentials")] = {30}


@pytest.mark.usefixtures("composed")
def test_live_references_resolve_by_id_across_organizations(fake_aap: Any) -> None:
    """B1: a project in another organization and a credential in none are not missing."""
    _across_organizations(fake_aap)
    result = _lint()
    assert result.exit_code == 0, result.output
    assert _rows(result) == []


@pytest.mark.usefixtures("composed")
def test_rules_still_judge_a_project_in_another_organization(fake_aap: Any) -> None:
    _across_organizations(fake_aap)
    fake_aap.store["job_templates"][100].update(playbook="deploy.yml", scm_branch="feature")
    assert _found(_lint()) == [
        ("awx.branch-override-ignored", "JobTemplate/Platform/deploy"),
        ("awx.playbook-missing", "JobTemplate/Platform/deploy"),
    ]


@pytest.mark.usefixtures("composed")
def test_a_fix_applies_with_yes(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    rows = _rows(_lint())
    fix = next(row["fix"] for row in rows if row["rule"] == "awx.ignored-launch-field")
    applied = invoke_cli(app, [*fix[1:], "--yes"])
    assert applied.exit_code == 0, applied.output
    assert fake_aap.store["job_templates"][100]["ask_limit_on_launch"] is True
    assert "awx.ignored-launch-field" not in {rule for rule, _ in _found(_lint("--refresh"))}


@pytest.mark.usefixtures("composed")
def test_kind_narrows_the_report_never_the_load(fake_aap: Any) -> None:
    """S14: the same requests, and the same snapshot, with and without --kind."""
    seed_controller(fake_aap)
    _lint("--refresh")
    full = _requests(fake_aap)
    [file] = _snapshot_files()
    snapshot = _kept_snapshot(file)
    narrowed = _lint("--refresh", "--kind", "job-templates")
    assert _requests(fake_aap)[len(full) :] == full
    assert _kept_snapshot(file) == snapshot
    assert {target for _, target in _found(narrowed)} == {
        "JobTemplate/Platform/deploy-web",
        "JobTemplate/Platform/orphan",
    }


def _namesake_schedules(fake: Any) -> None:
    """Templates ``a`` and ``b`` each with a schedule ``nightly`` setting a limit they ignore."""
    fake.seed("organizations", id=1, name="Platform")
    platform = {"organization": {"id": 1, "name": "Platform"}}
    fake.seed(
        "projects", id=10, name="web", organization=1, status="successful", scm_type="git",
        scm_url="https://git.example.com/web.git", summary_fields=platform,
    )  # fmt: skip
    fake.playbooks[10] = ["site.yml"]
    fake.seed(
        "inventories", id=20, name="prod", organization=1, kind="", total_hosts=2,
        summary_fields=platform,
    )  # fmt: skip
    for id_, name in ((100, "a"), (101, "b")):
        fake.seed(
            "job_templates", id=id_, name=name, organization=1, project=10, inventory=20,
            playbook="site.yml", ask_limit_on_launch=False, summary_fields=platform,
        )  # fmt: skip
        fake.seed(
            "schedules", id=id_ + 300, name="nightly", unified_job_template=id_,
            rrule="DTSTART:20260101T000000Z RRULE:FREQ=DAILY", limit="web1",
            summary_fields={"unified_job_template": {
                "id": id_, "name": name, "unified_job_type": "job",
                "organization_name": "Platform",
            }},
        )  # fmt: skip


@pytest.mark.usefixtures("composed")
def test_a_query_reports_the_resources_it_matched_not_their_namesakes(fake_aap: Any) -> None:
    """S2: both schedules' target is Schedule/Platform/nightly; only one was matched."""
    _namesake_schedules(fake_aap)
    assert len(_rows(_lint())) == 2
    result = _lint("--kind", "schedules", "--filter", "unified_job_template=100")
    assert [row["location"]["id"] for row in _rows(result)] == ["schedules/400"]


@pytest.mark.usefixtures("composed")
def test_filter_reports_only_what_the_controller_lists(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    result = _lint("--kind", "job-templates", "--filter", "name=deploy-web")
    assert sorted({target for _, target in _found(result)}) == ["JobTemplate/Platform/deploy-web"]
    search = _lint("--search", "orphan")
    assert _found(search) == [("awx.missing-reference", "JobTemplate/Platform/orphan")]


@pytest.mark.usefixtures("composed")
def test_a_filter_that_matches_nothing_says_so_and_exits_0(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    result = invoke_cli(app, ["lint", "--filter", "name=nothing-like-it"])
    assert result.exit_code == 0, result.output
    assert "no resource matches --filter/--search; nothing to report" in result.stderr
    assert result.stdout == ""


# --- usage --------------------------------------------------------------------------


@pytest.mark.usefixtures("composed")
@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--offline"], "--offline reads FILES only"),
        (["f.yml", "--refresh"], "--refresh and --cached apply to the controller snapshot"),
        (["f.yml", "--cached"], "--refresh and --cached apply to the controller snapshot"),
        (["f.yml", "--filter", "name=x"], "--filter and --search ask the controller"),
        (["--cached", "--search", "x"], "narrow with --kind or --organization"),
        (["--kind", "gadgets"], "--kind: unknown kind 'gadgets'"),
        (["--org", "A", "--filter", "organization__name=A"], "do not combine --org"),
        (["--kind", "projects", "--filter", "playbook=x"],
         "--filter: 'playbook' is not a field of Project"),
        (["--filter", "colour=x"], "--filter: 'colour' is not a field of any kind"),
    ],
    ids=["offline-alone", "refresh-files", "cached-files", "filter-files", "search-cached",
         "unknown-kind", "org-twice", "filter-not-on-kind", "filter-on-no-kind"],
)  # fmt: skip
def test_usage_mistakes_exit_2_before_anything_is_read(
    fake_aap: Any, tmp_path: Path, args: list[str], message: str
) -> None:
    (tmp_path / "f.yml").write_text(CLEAN, encoding="utf-8")
    result = invoke_cli(app, ["lint", *args])
    assert result.exit_code == 2, result.output
    assert message in result.stderr
    assert _requests(fake_aap) == []


# --- another plugin's rules over the same subject -----------------------------------


@rule(
    "vault-credential",
    reads=AwxResources,
    severity="warning",
    summary="Every job template carries a credential.",
)
def _vault_credential(resources: AwxResources) -> Iterator[Finding]:
    from untaped_awx.api import JobTemplateDoc, location_of, target_of
    from untaped_lint.api import finding

    for item in resources.of("JobTemplate"):
        if not JobTemplateDoc.model_validate(item.resource.spec).credentials:
            yield finding(
                _vault_credential, target_of(item), "no credential", location=location_of(item)
            )


class _HouseRules(Rules):
    RULES = (_vault_credential,)


@rule("boom", reads=AwxResources, severity="error", summary="Raises.")
def _boom(resources: AwxResources) -> Iterator[Finding]:
    raise RuntimeError("cannot judge")
    yield  # pragma: no cover - a generator


class _Raising(Rules):
    RULES = (_boom,)


def test_a_third_party_rule_that_raises_exits_1_and_awx_still_reports(tmp_path: Path) -> None:
    with compose_with("awx", "lint", provides={"house": [_Raising()]}):
        result = _lint(str(_files(tmp_path, templates=DOCUMENTS)), "--offline")
    assert result.exit_code == 1, result.output
    assert len(_found(result)) == 3  # awx's rows still print
    assert "house.boom" in result.stderr


@rule("rewrite", reads=AwxResources, severity="warning", summary="Mutates what it reads.")
def _rewrite(resources: AwxResources) -> Iterator[Finding]:
    for item in resources.items:
        item.resource.spec["project"] = "elsewhere"
    return iter(())


@rule("project", reads=AwxResources, severity="warning", summary="Says each project.")
def _project(resources: AwxResources) -> Iterator[Finding]:
    from untaped_awx.api import location_of, target_of
    from untaped_lint.api import finding

    for item in resources.of("JobTemplate"):
        project = item.resource.spec.get("project")
        yield finding(_project, target_of(item), f"project {project}", location=location_of(item))


class _Rewriting(Rules):
    RULES = (_rewrite,)


class _Reading(Rules):
    RULES = (_project,)


def test_a_provider_mutating_its_subject_leaks_nothing(tmp_path: Path) -> None:
    """S35: what one provider does to the snapshot, no other provider or awx sees."""
    providers = {"aaa": [_Rewriting()], "zzz": [_Reading()]}
    with compose_with("awx", "lint", provides=providers):
        result = _lint(str(_files(tmp_path, clean=CLEAN)), "--offline")
    assert result.exit_code == 0, result.output
    assert [row["message"] for row in _rows(result)] == ["project web"]


def test_a_quarantined_awx_has_no_rules_to_run_and_exits_4(tmp_path: Path) -> None:
    """S26: awx's lint offer raises and no other provider has rules."""
    import dataclasses

    def broken() -> tuple[()]:
        raise RuntimeError("cannot offer")

    spec = dataclasses.replace(SPEC, provides={"lint": broken})
    with compose_with(spec, "lint"):
        result = _lint(str(_files(tmp_path, clean=CLEAN)), "--offline")
    assert result.exit_code == 4, result.output
    assert "awx's rules did not run: quarantined" in result.stderr


def test_another_plugins_rules_read_the_awx_subject(tmp_path: Path) -> None:
    with compose_with("awx", "lint", provides={"house": [_HouseRules()]}):
        result = _lint(str(_files(tmp_path, clean=CLEAN)), "--offline")
    assert _found(result) == [("house.vault-credential", "JobTemplate/Platform/deploy-web")]
    assert _rows(result)[0]["source"]["plugin"] == "house"


@pytest.mark.usefixtures("composed")
def test_the_fill_stays_within_its_request_budget(fake_aap: Any) -> None:
    """S17: one list per kind, then only what a row lacks; no record read, no name lookup.

    Measured on this fixture: 15 lists, 18 sub-endpoint pages (a template's
    survey and members, a workflow's nodes and theirs, an inventory's
    instance groups) and one playbooks read, 34 requests.
    """
    seed_controller(fake_aap)
    _lint()
    paths = [call.request.url.path.removeprefix("/api/v2/") for call in fake_aap.router.calls]
    queries = [call.request.url.params for call in fake_aap.router.calls]
    assert all(call.request.method == "GET" for call in fake_aap.router.calls)
    assert not [path for path in paths if path.strip("/").split("/")[-1].isdigit()]
    assert not [query for query in queries if "name" in query or "id" in query]
    assert sum(1 for path in paths if path.count("/") == 1) == 15
    assert paths.count("projects/10/playbooks/") == 1
    assert len(paths) == 34
