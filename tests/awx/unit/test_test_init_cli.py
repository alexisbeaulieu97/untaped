"""``untaped awx test init``: a commented starter suite from a template's launch settings."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

if TYPE_CHECKING:  # pragma: no cover — pytest --import-mode=importlib hides 'tests'
    from tests.conftest import FakeAap
else:
    FakeAap = object  # type: ignore[assignment,misc]


@pytest.fixture
def cli() -> CliInvoker:
    return CliInvoker()


_SURVEY: dict[str, Any] = {
    "name": "",
    "description": "",
    "spec": [
        {"variable": "app_version", "type": "text", "required": True, "default": "1.0"},
        {
            "variable": "environment",
            "type": "multiplechoice",
            "required": True,
            "default": "",
            "choices": ["staging", "production"],
        },
        {
            "variable": "zones",
            "type": "multiselect",
            "required": True,
            "default": "",
            "choices": "eu\nus",
        },
        {"variable": "replicas", "type": "integer", "required": True, "default": ""},
        {"variable": "db_password", "type": "password", "required": True, "default": "s3cret"},
        {"variable": "api_token", "type": "password", "required": True, "default": ""},
        {"variable": "region", "type": "text", "required": False, "default": "us-east"},
    ],
}


def _seed_deploy(fake: FakeAap, **fields: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed(
        "job_templates",
        name="Deploy app",
        organization=1,
        summary_fields={"organization": {"id": 1, "name": "Default"}},
        **fields,
    )


def _seed_surveyed_deploy(fake: FakeAap) -> None:
    _seed_deploy(
        fake,
        survey_enabled=True,
        survey_spec=_SURVEY,
        variables_needed_to_start=["environment", "zones", "replicas"],
        ask_limit_on_launch=True,
        ask_scm_branch_on_launch=True,
    )


def _body(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def test_init_writes_a_starter_suite_from_the_survey_and_prompts(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_surveyed_deploy(fake_aap)
    out = tmp_path / "deploy.yml"

    result = cli.invoke(app, ["test", "init", "Deploy app", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(out)
    assert f"awx test validate {out}" in result.stderr
    text = out.read_text()
    body = _body(out)
    assert body["kind"] == "AwxTestSuite"
    assert body["name"] == "deploy-app"
    assert body["jobTemplate"] == "Deploy app"
    assert body["organization"] == "Default"
    assert body["defaults"]["launch"]["extra_vars"] == {
        "app_version": "1.0",
        "environment": "staging",
        "zones": ["eu"],
        "replicas": "TODO",
        "db_password": "$encrypted$",
        "api_token": "TODO",
    }
    assert body["cases"] == {"smoke": {"expect": {"status": "successful"}}}
    # Every filled variable says where its value came from. A stored password
    # default stays AWX's placeholder, which AWX replaces at launch.
    assert "# survey: required, text" in text
    assert "# survey: required, multiplechoice [staging, production]" in text
    assert "s3cret" not in text
    assert "# survey: required, password (AWX's stored default)" in text
    assert "# region: optional survey variable (text)" in text
    assert "limit, scm_branch" in text


def test_init_output_passes_list_and_validate(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_surveyed_deploy(fake_aap)
    out = tmp_path / "deploy.yml"
    assert cli.invoke(app, ["test", "init", "Deploy app", "--out", str(out)]).exit_code == 0

    listed = cli.invoke(app, ["test", "list", str(out), "--format", "json"])
    validated = cli.invoke(app, ["test", "validate", str(out)])

    assert listed.exit_code == 0, listed.output
    assert validated.exit_code == 0, validated.output
    assert "1 case validated" in validated.stderr


def test_init_without_a_survey_writes_a_minimal_suite_that_validates(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_deploy(fake_aap)
    out = tmp_path / "deploy.yml"

    result = cli.invoke(app, ["test", "init", "Deploy app", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert "defaults" not in _body(out)
    assert "# Launch prompts: none" in out.read_text()
    assert cli.invoke(app, ["test", "validate", str(out)]).exit_code == 0


def test_init_escapes_jinja_in_values(cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path) -> None:
    _seed_deploy(
        fake_aap,
        survey_enabled=True,
        survey_spec={
            "spec": [
                {"variable": "greeting", "type": "text", "required": True, "default": "{{ hi }}"},
                {
                    "variable": "mode",
                    "type": "multiplechoice",
                    "required": True,
                    "choices": ["{% raw %}", "{# b #}"],
                },
            ]
        },
    )
    out = tmp_path / "deploy.yml"
    assert cli.invoke(app, ["test", "init", "Deploy app", "--out", str(out)]).exit_code == 0

    listed = cli.invoke(app, ["test", "list", str(out), "--format", "json"])

    assert listed.exit_code == 0, listed.output


def test_init_defaults_to_the_suite_directory_at_the_git_root(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_deploy(fake_aap)
    repo = tmp_path / "repo"
    (repo / "roles").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo / "roles")

    result = cli.invoke(app, ["test", "init", "Deploy app"])

    expected = repo / ".untaped" / "awx" / "tests" / "deploy-app.yml"
    assert result.exit_code == 0, result.output
    assert Path(result.stdout.strip()).resolve() == expected.resolve()
    assert _body(expected)["jobTemplate"] == "Deploy app"


def test_init_refuses_to_overwrite_a_file(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_deploy(fake_aap)
    out = tmp_path / "deploy.yml"
    out.write_text("keep me\n")

    result = cli.invoke(app, ["test", "init", "Deploy app", "--out", str(out)])

    assert result.exit_code == 1
    assert f"{out} already exists" in result.stderr
    assert out.read_text() == "keep me\n"


def test_init_of_an_unknown_template_fails_without_writing(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    out = tmp_path / "deploy.yml"

    result = cli.invoke(app, ["test", "init", "Nope", "--out", str(out)])

    assert result.exit_code == 1
    assert "'Nope'" in result.stderr
    assert not out.exists()
