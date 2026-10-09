"""``awx test run|validate --source-ref REF``: suites run temporary copies of the repo's specs."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from untaped.testing import CliInvoker
from untaped_awx.application.suites.runner import RunTestSuite
from untaped_awx.application.suites.temporary_set import TemporarySets
from untaped_awx.cli import app
from untaped_awx.cli.context import AwxContext

if TYPE_CHECKING:  # pragma: no cover — pytest --import-mode=importlib hides 'tests'
    from awx.conftest import FakeAap
else:
    FakeAap = object  # type: ignore[assignment,misc]

_DEPLOY = """\
kind: JobTemplate
metadata: {name: Deploy, organization: Default}
spec:
  project: Playbooks
  playbook: deploy.yml
  inventory: Prod
  credentials: [%s]
  scm_branch: main
  webhook_service: github
"""
_SUITE = """\
kind: AwxTestSuite
name: deploy
jobTemplate: Deploy
cases:
  web:
    launch: {limit: web, inventory: Test}
"""
_RELEASE = """\
kind: WorkflowJobTemplate
metadata: {name: Release, organization: Default}
spec:
  nodes:
    - id: deploy
      run: {job_template: Deploy}
"""
_COPY = re.compile(r"Deploy \[untaped-test [0-9a-f]{7} [a-z0-9]{4}\]")


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A pushed ``feature/x`` branch holding a job template spec and its suite."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)], check=True)
    repo = tmp_path / "playbooks"
    subprocess.run(["git", "clone", "-q", str(bare), str(repo)], check=True, capture_output=True)
    for key, value in [("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")]:
        _git(repo, "config", key, value)
    _git(repo, "checkout", "-q", "-b", "feature/x")
    _commit(repo, {"templates/deploy.yml": _DEPLOY % "Machine", "tests/deploy.yml": _SUITE})
    monkeypatch.chdir(repo)
    return repo


def _commit(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / ".untaped" / "awx" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "specs")
    _git(repo, "push", "-q", "-u", "origin", "HEAD")


@pytest.fixture
def aap(fake_aap: FakeAap, monkeypatch: pytest.MonkeyPatch) -> FakeAap:
    monkeypatch.setattr(AwxContext, "pause", lambda self, seconds: None)
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects", id=5, name="Playbooks", organization=1, allow_override=True, scm_revision="c0"
    )
    fake_aap.seed("inventories", id=20, name="Prod", organization=1, kind="")
    fake_aap.seed("inventories", id=21, name="Test", organization=1, kind="")
    fake_aap.seed("credentials", id=30, name="Machine", organization=1)
    return fake_aap


def _invoke(*args: str) -> Any:
    return CliInvoker().invoke(app, ["test", *args])


def _run(*args: str) -> tuple[int, list[dict[str, Any]], str]:
    result = _invoke("run", "--source-ref", "HEAD", "--parallel", "1", "-f", "json", *args)
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    return result.exit_code, rows, result.stderr


def _errors(stderr: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]
    return [line for line in lines if line["level"] == "error"]


def _launches(aap: FakeAap) -> list[tuple[str, int, dict[str, Any]]]:
    calls = aap.actions_called
    return [(path, id_, body) for path, id_, action, body in calls if action == "launch"]


def test_a_suite_runs_a_pinned_copy_of_its_spec_which_is_deleted_after(
    aap: FakeAap, repo: Path
) -> None:
    sha = _git(repo, "rev-parse", "HEAD")

    code, [row], stderr = _run()

    assert code == 0, stderr
    assert row["result"] == "pass"
    assert row["scm_branch"] == sha  # the copy's own branch: the commit
    [(path, template_id, body)] = _launches(aap)
    assert path == "job_templates"
    # The copy prompts for what the case sets, and is not passed the ref.
    assert body == {"limit": "web", "inventory": 21}
    assert aap.list_records("job_templates") == []
    assert re.search(rf"created JobTemplate '{_COPY.pattern}'", stderr)
    assert "prompts enabled: ask_inventory_on_launch, ask_limit_on_launch" in stderr
    assert re.search(rf"deleted JobTemplate '{_COPY.pattern}'", stderr)
    assert template_id not in {record["id"] for record in aap.list_records("job_templates")}


def test_keep_keeps_the_copy_and_names_it(aap: FakeAap, repo: Path) -> None:
    sha = _git(repo, "rev-parse", "HEAD")

    code, _, stderr = _run("--keep")

    assert code == 0, stderr
    [copy] = aap.list_records("job_templates")
    assert _COPY.fullmatch(copy["name"])
    assert f"kept JobTemplate '{copy['name']}' (id {copy['id']})" in stderr
    run_id = copy["name"][-5:-1]
    assert re.fullmatch(
        rf"untaped-test run={run_id} ref=HEAD sha={sha[:7]} created=\S+Z", copy["description"]
    )
    assert (copy["scm_branch"], copy["project"], copy["inventory"]) == (sha, 5, 20)
    assert copy["ask_limit_on_launch"] is True
    assert copy["ask_inventory_on_launch"] is True
    assert "webhook_service" not in copy
    assert set(aap.memberships[("job_templates", copy["id"], "credentials")]) == {30}


def test_validate_prints_the_plan_and_writes_nothing(aap: FakeAap, repo: Path) -> None:
    result = _invoke("validate", "--source-ref", "HEAD", "-f", "json")

    assert result.exit_code == 0, result.stderr
    [planned] = json.loads(result.stdout)
    assert _COPY.fullmatch(planned["name"])
    assert planned | {"name": None, "run_id": None, "created_at": None} == {
        "id": None,
        "name": None,
        "kind": "JobTemplate",
        "template": "Deploy",
        "organization": "Default",
        "run_id": None,
        "ref": "HEAD",
        "sha": _git(repo, "rev-parse", "--short=7", "HEAD"),
        "created_at": None,
        "path": "HEAD:.untaped/awx/templates/deploy.yml",
        "prompts": ["ask_inventory_on_launch", "ask_limit_on_launch"],
        "action": "planned",
        "detail": None,
    }
    assert "1 case validated" in result.stderr
    assert aap.list_records("job_templates") == []


def test_dry_run_checks_like_validate_and_launches_nothing(aap: FakeAap, repo: Path) -> None:
    code, [planned], stderr = _run("--dry-run")

    assert code == 0, stderr
    assert planned["action"] == "planned"
    assert (aap.list_records("job_templates"), _launches(aap)) == ([], [])


def test_validate_without_source_ref_checks_the_scm_branch_it_would_pass(
    aap: FakeAap, repo: Path
) -> None:
    prompts = {"ask_limit_on_launch": True, "ask_inventory_on_launch": True}
    aap.seed("job_templates", name="Deploy", organization=1, project=5, **prompts)
    suite = ".untaped/awx/tests/deploy.yml"

    assert _invoke("validate", suite).exit_code == 0
    result = _invoke("validate", suite, "--scm-branch", "main")

    assert result.exit_code == 1
    assert "does not prompt for scm_branch" in result.stderr
    assert _launches(aap) == []


def test_a_missing_link_fails_before_anything_is_created(aap: FakeAap, repo: Path) -> None:
    _commit(repo, {"templates/deploy.yml": _DEPLOY % "Machin"})

    code, rows, stderr = _run()

    assert (code, rows) == (1, [])
    [error] = _errors(stderr)
    assert error["message"].startswith(
        "cannot provision the temporary test set of HEAD (nothing was created); nothing launched:"
    )
    assert "Machin" in error["message"] and "did you mean 'Machine'?" in error["message"]
    assert (error["category"], error["system"]) == ("not_found", "awx.suite")
    assert not any(call.request.method == "POST" for call in aap.router.calls)

    validated = _invoke("validate", "--source-ref", "HEAD")
    assert validated.exit_code == 1
    assert "did you mean 'Machine'?" in validated.stderr


def test_a_project_that_does_not_allow_branch_override_is_refused(aap: FakeAap, repo: Path) -> None:
    aap.get_record("projects", 5)["allow_override"] = False

    code, rows, stderr = _run()

    assert (code, rows) == (1, [])
    [error] = _errors(stderr)
    assert "project 'Playbooks' does not allow branch override" in error["message"]
    assert (error["category"], error["system"]) == ("invalid", "awx.scm")
    assert aap.list_records("job_templates") == []


def test_a_copy_name_already_taken_is_refused(
    aap: FakeAap, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("secrets.choice", lambda alphabet: "a")
    sha7 = _git(repo, "rev-parse", "--short=7", "HEAD")
    aap.seed("job_templates", name=f"Deploy [untaped-test {sha7} aaaa]", organization=1)

    result = _invoke("validate", "--source-ref", "HEAD")

    assert result.exit_code == 1
    [error] = _errors(result.stderr)
    assert f"JobTemplate 'Deploy [untaped-test {sha7} aaaa]': already exists" in error["message"]
    assert (error["category"], error["system"]) == ("conflict", "awx.suite")


def test_a_spec_in_another_organization_is_named_and_does_not_bind(
    aap: FakeAap, repo: Path
) -> None:
    _commit(
        repo,
        {
            "templates/deploy.yml": _DEPLOY.replace("Default", "Ops") % "Machine",
            "tests/deploy.yml": _SUITE.replace("cases:", "organization: Default\ncases:"),
        },
    )

    code, rows, stderr = _run()

    assert (code, rows) == (1, [])
    assert (
        "deploy: the spec of JobTemplate 'Deploy' (HEAD:.untaped/awx/templates/deploy.yml) "
        "is not in organization Default"
    ) in stderr
    assert "deploy: JobTemplate not found: 'Deploy'" in _errors(stderr)[0]["message"]


def test_a_template_without_a_spec_that_does_not_exist_is_refused(aap: FakeAap, repo: Path) -> None:
    _commit(repo, {"tests/gone.yml": "kind: AwxTestSuite\njobTemplate: Gone\ncases: {c: {}}\n"})

    code, rows, stderr = _run()

    assert (code, rows) == (1, [])
    [error] = _errors(stderr)
    assert "gone: JobTemplate not found: 'Gone'" in error["message"]
    assert aap.list_records("job_templates") == []


def test_a_template_without_a_spec_must_prompt_for_the_branch(aap: FakeAap, repo: Path) -> None:
    other = aap.seed("job_templates", name="Other", organization=1, project=5)
    _commit(repo, {"tests/other.yml": "kind: AwxTestSuite\njobTemplate: Other\ncases: {c: {}}\n"})

    code, rows, stderr = _run()

    assert (code, rows) == (1, [])
    [error] = _errors(stderr)
    assert "other: JobTemplate 'Other' has no spec in the repository at HEAD" in error["message"]
    sha7 = _git(repo, "rev-parse", "--short=7", "HEAD")
    assert f"other: runs AWX's JobTemplate 'Other' at {sha7} (no spec)" in stderr
    assert error["hint"] == (
        "add its spec under `.untaped/awx/` or enable `ask_scm_branch_on_launch`"
    )
    assert (error["category"], error["system"]) == ("invalid", "awx.scm")
    assert [record["id"] for record in aap.list_records("job_templates")] == [other["id"]]

    other["ask_scm_branch_on_launch"] = True
    code, rows, stderr = _run()

    assert code == 0, stderr
    sha = _git(repo, "rev-parse", "HEAD")
    assert [body for _, id_, body in _launches(aap) if id_ == other["id"]] == [{"scm_branch": sha}]


def test_a_workflow_copy_runs_the_copy_of_its_job_template(aap: FakeAap, repo: Path) -> None:
    _commit(
        repo,
        {
            "workflows/release.yml": _RELEASE,
            "tests/release.yml": (
                "kind: AwxTestSuite\nworkflowTemplate: Release\ncases: {happy: {}}\n"
            ),
        },
    )

    code, rows, stderr = _run("--case", "release/happy", "--keep")

    assert code == 0, stderr
    assert [row["result"] for row in rows] == ["pass"]
    [job_template] = aap.list_records("job_templates")
    [workflow] = aap.list_records("workflow_job_templates")
    assert _COPY.fullmatch(job_template["name"])
    assert workflow["name"].startswith("Release [untaped-test ")
    [node] = aap.list_records("workflow_nodes")
    assert node["unified_job_template"] == job_template["id"]
    kept = [line for line in stderr.splitlines() if "kept" in line]
    assert "WorkflowJobTemplate" in kept[0] and "JobTemplate" in kept[1]


def test_validate_checks_a_workflow_case_against_the_copys_nodes(aap: FakeAap, repo: Path) -> None:
    gated = _RELEASE + "      success: [approve]\n    - id: approve\n      approval: {name: Go}\n"
    suite = (
        "kind: AwxTestSuite\nworkflowTemplate: Release\ncases:\n"
        "  happy: {}\n  typo: {expect: {nodes: {deplyo: {status: successful}}}}\n"
    )
    _commit(repo, {"workflows/release.yml": gated, "tests/release.yml": suite})

    result = _invoke("validate", "--source-ref", "HEAD", ".untaped/awx/tests/release.yml")

    assert result.exit_code == 1
    [error] = _errors(result.stderr)
    assert error["item"] == "release/typo"
    assert error["message"].startswith("workflow node not found: 'deplyo'")
    assert "did you mean 'deploy'?" in error["message"]
    assert "the workflow has approval nodes (approve)" in result.stderr
    assert aap.list_records("workflow_job_templates") == []


def test_an_unpushed_commit_is_refused(aap: FakeAap, repo: Path) -> None:
    _git(repo, "commit", "-q", "--allow-empty", "-m", "local")

    code, rows, stderr = _run()

    assert (code, rows) == (4, [])
    [error] = _errors(stderr)
    assert (error["category"], error["system"]) == ("config", "git")


def test_a_teardown_failure_is_a_warning_and_never_changes_the_result(
    aap: FakeAap, repo: Path
) -> None:
    aap.delete_error = 403

    code, [row], stderr = _run()

    assert code == 0, stderr
    assert row["result"] == "pass"
    [copy] = aap.list_records("job_templates")
    warnings = [json.loads(line) for line in stderr.splitlines() if '"warning"' in line]
    assert any(
        f"JobTemplate '{copy['name']}': left behind by teardown" in line["message"]
        for line in warnings
    )
    run_id = copy["name"][-5:-1]
    assert f"run `untaped awx test prune --run {run_id} --older-than 0`" in stderr


def test_a_second_ctrl_c_during_teardown_still_reports_the_results_and_what_is_left(
    aap: FakeAap, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(self: Any, copy: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(TemporarySets, "delete", interrupted)

    code, [row], stderr = _run()

    assert (code, row["result"]) == (0, "pass")
    [copy] = aap.list_records("job_templates")
    assert f"JobTemplate '{copy['name']}': left behind by teardown, teardown interrupted" in stderr
    assert "--older-than 0" in stderr


def test_a_workflow_node_running_a_template_that_would_not_run_the_commit_is_refused(
    aap: FakeAap, repo: Path
) -> None:
    legacy = aap.seed("job_templates", name="Legacy", organization=1, project=5)
    release = _RELEASE + "    - id: old\n      run: {job_template: Legacy}\n"
    suite = "kind: AwxTestSuite\nworkflowTemplate: Release\ncases: {happy: {}}\n"
    _commit(repo, {"workflows/release.yml": release, "tests/release.yml": suite})

    code, rows, stderr = _run("--case", "release/happy")

    assert (code, rows) == (1, [])
    [error] = _errors(stderr)
    assert "release (node old): JobTemplate 'Legacy' has no spec" in error["message"]
    assert (error["category"], error["system"]) == ("invalid", "awx.scm")
    assert [record["id"] for record in aap.list_records("job_templates")] == [legacy["id"]]

    legacy["ask_scm_branch_on_launch"] = True
    code, rows, stderr = _run("--case", "release/happy")

    assert code == 0, stderr


def test_the_nodes_of_a_workflow_awx_holds_must_run_the_commit_too(
    aap: FakeAap, repo: Path
) -> None:
    workflow = aap.seed(
        "workflow_job_templates", name="Nightly", organization=1, ask_scm_branch_on_launch=True
    )
    job = aap.seed("job_templates", name="Backup", organization=1, project=5)
    aap.seed(
        "workflow_nodes",
        workflow_job_template=workflow["id"],
        identifier="backup",
        unified_job_template=job["id"],
    )
    suite = "kind: AwxTestSuite\nworkflowTemplate: Nightly\ncases: {c: {}}\n"
    _commit(repo, {"tests/nightly.yml": suite})

    code, rows, stderr = _run("--case", "nightly/c")

    assert (code, rows) == (1, [])
    [error] = _errors(stderr)
    assert "nightly (node backup): JobTemplate 'Backup' has no spec" in error["message"]
    assert "cannot run HEAD; nothing launched:" in error["message"]
    assert "nightly: runs AWX's WorkflowJobTemplate 'Nightly' at" in stderr


def test_a_project_that_cannot_be_read_is_part_of_the_refusal(aap: FakeAap, repo: Path) -> None:
    aap.detail_errors[("projects", 5)] = 503

    code, rows, stderr = _run()

    assert (code, rows) == (5, [])
    [error] = _errors(stderr)
    assert error["message"].startswith("cannot provision the temporary test set of HEAD")
    assert (error["category"], error["system"]) == ("unavailable", "awx.controller")


def test_a_copy_awx_does_not_store_as_written_is_refused(aap: FakeAap, repo: Path) -> None:
    aap.ignored_write_fields = {"playbook"}

    code, rows, stderr = _run()

    assert (code, rows) == (1, [])
    [error] = _errors(stderr)
    assert "AWX did not store it as the spec asks (unverified 1 field: playbook" in error["message"]
    assert error["hint"] == "rerun with --keep to inspect the copy, then fix the spec"
    assert "--allow-unverified" not in stderr
    assert aap.list_records("job_templates") == []
    assert _launches(aap) == []


def test_a_provisioning_failure_keeps_the_controllers_attribution(aap: FakeAap, repo: Path) -> None:
    aap.forbidden_associate_ids = {30}

    code, rows, stderr = _run()

    assert (code, rows) == (4, [])
    [error] = _errors(stderr)
    assert error["message"].startswith(
        "cannot provision the temporary test set of HEAD (the copies created are torn down)"
    )
    assert (error["category"], error["system"]) == ("permission", "awx.credentials")
    assert "Job Template Admin" in error["hint"]
    assert aap.list_records("job_templates") == []  # the half-made copy is gone
    assert _launches(aap) == []


def test_an_interrupted_run_still_tears_down(
    aap: FakeAap, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(self: RunTestSuite, *args: Any, **kwargs: Any) -> Any:
        assert aap.list_records("job_templates")  # the copy exists while the cases run
        raise KeyboardInterrupt

    monkeypatch.setattr(RunTestSuite, "__call__", interrupted)

    code, _, stderr = _run()

    assert code == 130
    assert aap.list_records("job_templates") == []
    assert "deleted JobTemplate" in stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--scm-branch", "main"], "--source-ref and --scm-branch cannot be combined"),
        (["--baseline", "main"], "--source-ref and --baseline cannot be combined"),
        (["--no-cancel"], "add --keep"),
        (["--dry-run", "--keep"], "--dry-run launches nothing, so --keep does not apply"),
        (["--dry-run", "--no-cancel"], "so --no-cancel does not apply"),
        (["--dry-run", "--baseline", "main"], "so --baseline does not apply"),
    ],
)
def test_flags_source_ref_cannot_go_with_are_refused(
    aap: FakeAap, repo: Path, args: list[str], message: str
) -> None:
    code, _, stderr = _run(*args)

    assert code == 2
    assert message in stderr


def test_keep_needs_source_ref(aap: FakeAap, repo: Path) -> None:
    result = _invoke("run", "--keep")

    assert result.exit_code == 2
    assert "--keep applies to --source-ref only" in result.stderr
