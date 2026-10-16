"""``untaped awx lint``: the three origins, end to end against the fake controller."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from awx.lint_support import CLEAN, DOCUMENTS, seed_controller
from untaped.sdk import plugin_dir
from untaped.testing import CliResult, compose_with, invoke_cli
from untaped_awx import SPEC
from untaped_awx.api import AwxResources
from untaped_awx.cli import app
from untaped_awx.cli.context import AwxContext
from untaped_awx.infrastructure.lint.snapshot_store import store_path
from untaped_lint.api import Finding, Rules, rule

pytestmark = pytest.mark.usefixtures("fresh_composition")


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


def _snapshot_file() -> Path:
    return store_path(plugin_dir(SPEC), profile="default", base_url="https://aap.example.com")


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
    snapshot = _snapshot_file()
    text = snapshot.read_text(encoding="utf-8")
    assert "hunter2" not in text
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
def test_a_snapshot_file_gone_from_under_its_sidecar_is_read_again(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    _lint()
    _snapshot_file().unlink()
    result = _lint()
    assert result.exit_code == 3, result.output
    assert _snapshot_file().exists()


@pytest.mark.usefixtures("composed")
def test_cached_with_nothing_kept_fails_without_asking(fake_aap: Any) -> None:
    seed_controller(fake_aap)
    result = _lint("--cached")
    assert result.exit_code != 0
    assert _requests(fake_aap) == []
    assert "controller resources" in result.stderr


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
