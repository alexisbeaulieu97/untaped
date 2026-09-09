"""Shared selection and confirmed mutation CLI behavior against the HTTP adapter."""

import json
from pathlib import Path
from typing import Any

import pytest

from awx.integration.support import KINDS, pipe, seed
from untaped.capabilities.awx.cli.commands import app
from untaped.testing import CliInvoker, ScriptedPromptBackend


@pytest.mark.parametrize(("cli", "path"), KINDS)
def test_patch_uses_fixed_id_and_reports_identity(fake_aap: Any, cli: str, path: str) -> None:
    seed(fake_aap, path)
    result = CliInvoker().invoke(
        app,
        [cli, "patch", "10", "--by-id", "--set", "description=new", "--yes", "--format", "json"],
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert fake_aap.get_record(path, 10)["description"] == "new"
    row = json.loads(result.stdout)[0]
    assert row["id"] == 10
    assert row["action"] == "updated"
    assert "scope" in row
    assert "old" in result.stderr and "new" in result.stderr


@pytest.mark.parametrize(("cli", "path"), KINDS)
def test_get_query_and_list_names_share_selection(fake_aap: Any, cli: str, path: str) -> None:
    seed(fake_aap, path)
    for args in (["get", "--filter", "id=10"], ["list", "target"]):
        result = CliInvoker().invoke(app, [cli, *args, "--format", "json"])
        assert result.exit_code == 0, result.output + result.stderr
        assert [row["id"] for row in json.loads(result.stdout)] == [10]


@pytest.mark.parametrize("selection", [["target", "missing"], ["10", "999", "--by-id"]])
def test_patch_invalid_batch_never_writes(fake_aap: Any, selection: list[str]) -> None:
    seed(fake_aap, "projects")
    result = CliInvoker().invoke(
        app, ["projects", "patch", *selection, "--set", "description=new", "--yes"]
    )
    assert result.exit_code != 0
    assert fake_aap.get_record("projects", 10)["description"] == "old"


@pytest.mark.parametrize("answer", [True, False])
def test_patch_confirms_once_default_no(fake_aap: Any, answer: bool) -> None:
    seed(fake_aap, "projects")
    backend = ScriptedPromptBackend(confirms=[answer])
    result = CliInvoker().invoke(
        app,
        ["projects", "patch", "target", "--set", "description=new", "--format", "json"],
        interactive=True,
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert fake_aap.get_record("projects", 10)["description"] == ("new" if answer else "old")
    assert len(backend.calls) == 1


def test_apply_wrong_kind_rejects_complete_batch(fake_aap: Any, tmp_path: Path) -> None:
    seed(fake_aap, "projects")
    file = tmp_path / "mixed.yml"
    file.write_text(
        "kind: Project\nmetadata: {name: target, organization: Default}\n"
        "spec: {description: new}\n---\nkind: Host\nmetadata: {name: host}\nspec: {}\n"
    )
    result = CliInvoker().invoke(app, ["projects", "apply", str(file), "--yes"])
    assert result.exit_code != 0
    assert fake_aap.get_record("projects", 10)["description"] == "old"


@pytest.mark.parametrize("command", ["get", "list", "patch", "delete", "save"])
def test_typed_pipe_selects_id_not_stale_name(fake_aap: Any, command: str) -> None:
    seed(fake_aap, "projects")
    extras = (
        ["--set", "description=new", "--yes"]
        if command == "patch"
        else (["--dry-run"] if command == "delete" else [])
    )
    result = CliInvoker().invoke(
        app,
        ["projects", command, "--stdin", *extras, "--format", "json"],
        input=pipe("awx.project", 10),
    )
    assert result.exit_code == 0, result.output
    assert "target" in result.stdout


@pytest.mark.parametrize("command", ["patch", "delete", "get", "list", "save"])
def test_empty_pipe_is_clear_no_match(fake_aap: Any, command: str) -> None:
    seed(fake_aap, "projects")
    extras = ["--set", "description=new"] if command == "patch" else []
    result = CliInvoker().invoke(
        app, ["projects", command, "--stdin", *extras, "--format", "json"], input=""
    )
    assert result.exit_code == 0, result.output
    assert "No matching" in result.stderr
    assert fake_aap.get_record("projects", 10)["description"] == "old"


def test_delete_validates_entire_batch_before_any_delete(fake_aap: Any) -> None:
    seed(fake_aap, "projects")
    result = CliInvoker().invoke(app, ["projects", "delete", "10", "999", "--by-id", "--yes"])
    assert result.exit_code != 0
    assert 10 in fake_aap.store["projects"]


def test_delete_managed_source_guard_preflights_complete_batch(fake_aap: Any) -> None:
    seed(fake_aap, "inventory_sources")
    fake_aap.seed("inventory_sources", id=11, name="managed", inventory=2, source="constructed")
    result = CliInvoker().invoke(
        app, ["inventory-sources", "delete", "10", "11", "--by-id", "--yes"]
    )
    assert result.exit_code != 0
    assert 10 in fake_aap.store["inventory_sources"]


def test_delete_renders_async_receipt(fake_aap: Any) -> None:
    import httpx

    seed(fake_aap, "inventories")
    fake_aap.router.delete("https://aap.example.com/api/v2/inventories/10/").mock(
        return_value=httpx.Response(202)
    )
    result = CliInvoker().invoke(
        app, ["inventories", "delete", "10", "--by-id", "--yes", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "deletion_requested"


def test_membership_requires_confirmation_and_retains_unrelated(fake_aap: Any) -> None:
    seed(fake_aap, "groups")
    fake_aap.seed(
        "hosts",
        id=20,
        name="member",
        inventory=2,
        summary_fields={"inventory": {"id": 2, "name": "prod", "organization_name": "Default"}},
    )
    fake_aap.memberships[("groups", 10, "hosts")] = {99}
    backend = ScriptedPromptBackend(confirms=[True])
    result = CliInvoker().invoke(
        app,
        ["groups", "hosts", "add", "target", "member", "--format", "json"],
        interactive=True,
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output
    assert fake_aap.memberships[("groups", 10, "hosts")] == {20, 99}
    assert len(backend.calls) == 1


def test_save_multiple_ids_as_portable_documents(fake_aap: Any, tmp_path: Path) -> None:
    seed(fake_aap, "projects")
    fake_aap.seed("projects", id=11, name="second", organization=1, description="two")
    target = tmp_path / "saved.yml"
    result = CliInvoker().invoke(
        app, ["projects", "save", "10", "11", "--by-id", "--out", str(target)]
    )
    assert result.exit_code == 0, result.output
    assert "name: target" in target.read_text()
    assert "name: second" in target.read_text()


def test_membership_noop_never_prompts_or_posts(fake_aap: Any) -> None:
    seed(fake_aap, "groups")
    fake_aap.seed(
        "hosts",
        id=20,
        name="member",
        inventory=2,
        summary_fields={"inventory": {"name": "prod", "organization_name": "Default"}},
    )
    fake_aap.memberships[("groups", 10, "hosts")] = {20}
    result = CliInvoker().invoke(
        app, ["groups", "hosts", "add", "target", "member", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "unchanged"
    assert not any(call.request.method == "POST" for call in fake_aap.router.calls)


@pytest.mark.parametrize(
    "extra",
    [
        ["--yes", "--dry-run"],
        ["--allow-unverified"],
        ["--fail-fast"],
        ["--inventory", "prod"],
        ["--parallel", "0"],
    ],
)
def test_patch_rejects_inapplicable_or_conflicting_controls(
    fake_aap: Any, extra: list[str]
) -> None:
    seed(fake_aap, "projects")
    result = CliInvoker().invoke(
        app, ["projects", "patch", "target", "--set", "description=new", *extra]
    )
    assert result.exit_code != 0
    assert fake_aap.get_record("projects", 10)["description"] == "old"


@pytest.mark.parametrize(
    "kind,id_",
    [("awx.host", 10), ("awx.project", "10"), ("awx.project", True), ("awx.project", None)],
)
def test_patch_rejects_invalid_pipe_before_write(fake_aap: Any, kind: str, id_: Any) -> None:
    seed(fake_aap, "projects")
    result = CliInvoker().invoke(
        app,
        ["projects", "patch", "--stdin", "--set", "description=new", "--yes"],
        input=pipe(kind, id_),
    )
    assert result.exit_code != 0
    assert fake_aap.get_record("projects", 10)["description"] == "old"


@pytest.mark.parametrize("fmt", ["json", "yaml", "pipe", "table", "raw"])
def test_patch_secret_values_redacted_in_all_formats(fake_aap: Any, fmt: str) -> None:
    seed(fake_aap, "job_templates")
    fake_aap.store["job_templates"][10]["webhook_key"] = "old-secret-value"
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "target",
            "--set",
            "webhook_key=new-secret-value",
            "--yes",
            "--format",
            fmt,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "old-secret-value" not in result.output
    assert "new-secret-value" not in result.output
    assert fake_aap.get_record("job_templates", 10)["webhook_key"] == "new-secret-value"


@pytest.mark.parametrize("cli", ["organizations", "credentials", "credential-types"])
@pytest.mark.parametrize("verb", ["apply", "patch", "save", "delete"])
def test_readonly_kinds_reject_mutation_commands(cli: str, verb: str) -> None:
    result = CliInvoker().invoke(app, [cli, verb, "target"])
    assert result.exit_code != 0


def test_query_scope_search_filter_and_dedup(fake_aap: Any) -> None:
    seed(fake_aap, "projects")
    fake_aap.seed("organizations", id=7, name="Other")
    fake_aap.seed("projects", id=11, name="target", organization=7, description="old")
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "patch",
            "--search",
            "target",
            "--filter",
            "description=old",
            "--organization",
            "Default",
            "--set",
            "description=new",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert fake_aap.get_record("projects", 10)["description"] == "new"
    assert fake_aap.get_record("projects", 11)["description"] == "old"


def test_numeric_names_and_ids_remain_distinct(fake_aap: Any) -> None:
    seed(fake_aap, "projects")
    fake_aap.seed("projects", id=11, name="10", organization=1, description="old")
    result = CliInvoker().invoke(
        app,
        ["projects", "patch", "10", "10", "--set", "description=new", "--yes", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.stdout)] == [11]
    assert fake_aap.get_record("projects", 10)["description"] == "old"


@pytest.mark.parametrize("continue_", [False, True])
def test_patch_runtime_failure_stops_scheduling(
    fake_aap: Any, continue_: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    seed(fake_aap, "projects")
    fake_aap.seed("projects", id=11, name="second", organization=1, description="old")
    original = fake_aap._update
    monkeypatch.setattr(
        fake_aap,
        "_update",
        lambda path, id_, body: (
            httpx.Response(500, json={"detail": "failed"})
            if id_ == 10
            else original(path, id_, body)
        ),
    )
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "patch",
            "10",
            "11",
            "--by-id",
            "--set",
            "description=new",
            "--yes",
            "--format",
            "json",
            *(["--continue-on-error"] if continue_ else []),
        ],
    )
    assert result.exit_code != 0
    assert [row["action"] for row in json.loads(result.stdout)] == [
        "failed",
        "updated" if continue_ else "skipped",
    ]
    assert fake_aap.get_record("projects", 11)["description"] == ("new" if continue_ else "old")


@pytest.mark.parametrize("continue_", [False, True])
def test_delete_runtime_failure_stops_scheduling(
    fake_aap: Any, continue_: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    seed(fake_aap, "projects")
    fake_aap.seed("projects", id=11, name="second", organization=1)
    original = fake_aap._delete
    monkeypatch.setattr(
        fake_aap,
        "_delete",
        lambda path, id_: (
            httpx.Response(409, json={"detail": "in use"}) if id_ == 10 else original(path, id_)
        ),
    )
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "delete",
            "10",
            "11",
            "--by-id",
            "--yes",
            "--format",
            "json",
            *(["--continue-on-error"] if continue_ else []),
        ],
    )
    assert result.exit_code != 0
    assert [row["action"] for row in json.loads(result.stdout)] == [
        "failed",
        "deleted" if continue_ else "skipped",
    ]
    assert (11 in fake_aap.store["projects"]) is not continue_


@pytest.mark.parametrize("tty_available", [True, False])
def test_piped_confirmation_uses_controlling_terminal(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch, tty_available: bool
) -> None:
    import builtins
    import io

    seed(fake_aap, "projects")
    real_open = builtins.open

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    def open_terminal(file: Any, *args: Any, **kwargs: Any) -> Any:
        if file == "/dev/tty":
            if tty_available:
                return Terminal()
            raise OSError("no controlling terminal")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_terminal)
    backend = ScriptedPromptBackend(confirms=[True])
    result = CliInvoker().invoke(
        app,
        ["projects", "patch", "--stdin", "--set", "description=new"],
        input="target\n",
        prompt_backend=backend,
    )
    assert (result.exit_code == 0) is tty_available, result.output
    assert fake_aap.get_record("projects", 10)["description"] == ("new" if tty_available else "old")
    assert len(backend.calls) == int(tty_available)
    if not tty_available:
        assert "--yes or --dry-run" in result.stderr


@pytest.mark.parametrize("allow", [False, True])
def test_unverified_patch_status_and_exit(fake_aap: Any, allow: bool) -> None:
    seed(fake_aap, "projects")
    fake_aap.ignored_write_fields = {"description"}
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "patch",
            "target",
            "--set",
            "description=new",
            "--yes",
            "--format",
            "json",
            *(["--allow-unverified"] if allow else []),
        ],
    )
    assert (result.exit_code == 0) is allow, result.output
    row = json.loads(result.stdout)[0]
    assert row["unverified"] is True
    if not allow:
        assert row["action"] == "partial"
        assert row["partial"] is True


@pytest.mark.parametrize(
    "args",
    [
        ["--organization", "Default"],
        ["target", "--filter", "id=10"],
        ["--all", "--search", "target"],
    ],
)
def test_mutation_rejects_implicit_or_mixed_selection(fake_aap: Any, args: list[str]) -> None:
    seed(fake_aap, "projects")
    result = CliInvoker().invoke(
        app, ["projects", "patch", *args, "--set", "description=new", "--yes"]
    )
    assert result.exit_code != 0
    assert fake_aap.get_record("projects", 10)["description"] == "old"


def test_patch_map_is_top_level_replacement_with_set_precedence(
    fake_aap: Any, tmp_path: Path
) -> None:
    seed(fake_aap, "job_templates")
    fake_aap.store["job_templates"][10]["extra_vars"] = '{"retain": 1, "remove": 2}'
    patch = tmp_path / "patch.yml"
    patch.write_text("extra_vars: {wrong: 9}\nverbosity: 1\n")
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "target",
            "--patch-file",
            str(patch),
            "--set",
            'extra_vars={"retain": 1}',
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(fake_aap.get_record("job_templates", 10)["extra_vars"]) == {"retain": 1}
    assert fake_aap.get_record("job_templates", 10)["verbosity"] == 1


def test_save_masks_secrets_with_preservation_placeholder(fake_aap: Any, tmp_path: Path) -> None:
    seed(fake_aap, "job_templates")
    fake_aap.store["job_templates"][10]["webhook_key"] = "secret-for-export"
    output = tmp_path / "saved.yml"
    result = CliInvoker().invoke(app, ["job-templates", "save", "target", "--out", str(output)])
    assert result.exit_code == 0, result.output
    assert "secret-for-export" not in output.read_text()
    assert "$encrypted$" in output.read_text()


@pytest.mark.parametrize(("cli", "path"), KINDS)
@pytest.mark.parametrize("mode", ["names", "bare", "query", "all"])
def test_writable_kinds_share_patch_selection_modes(
    fake_aap: Any, cli: str, path: str, mode: str
) -> None:
    seed(fake_aap, path)
    args = {
        "names": ["target"],
        "bare": ["--stdin"],
        "query": ["--filter", "id=10"],
        "all": ["--all"],
    }[mode]
    result = CliInvoker().invoke(
        app,
        [cli, "patch", *args, "--set", "description=new", "--yes"],
        input="target\n" if mode == "bare" else None,
    )
    assert result.exit_code == 0, result.output
    assert fake_aap.get_record(path, 10)["description"] == "new"


def test_confirmation_executes_original_plan_and_detects_drift(fake_aap: Any) -> None:
    seed(fake_aap, "projects")

    class DriftBackend(ScriptedPromptBackend):
        def confirm(self, message: str, *, default: bool) -> bool:
            assert default is False
            fake_aap.store["projects"][10]["description"] = "concurrent change"
            return super().confirm(message, default=default)

    backend = DriftBackend(confirms=[True])
    result = CliInvoker().invoke(
        app,
        ["projects", "patch", "target", "--set", "description=new", "--format", "json"],
        interactive=True,
        prompt_backend=backend,
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)[0]["action"] == "conflict"
    assert fake_aap.get_record("projects", 10)["description"] == "concurrent change"
    assert not any(call.request.method == "PATCH" for call in fake_aap.router.calls)


def test_membership_already_absent_remove_does_not_prompt_or_write(fake_aap: Any) -> None:
    seed(fake_aap, "groups")
    fake_aap.seed(
        "hosts",
        id=20,
        name="member",
        inventory=2,
        summary_fields={"inventory": {"name": "prod", "organization_name": "Default"}},
    )
    result = CliInvoker().invoke(
        app, ["groups", "hosts", "remove", "target", "member", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "unchanged"
    assert not any(call.request.method == "POST" for call in fake_aap.router.calls)


@pytest.mark.parametrize("command", ["get", "list"])
@pytest.mark.parametrize("fmt", ["json", "yaml", "pipe", "table", "raw"])
@pytest.mark.parametrize("field", ["webhook_key", "survey_spec"])
def test_reads_redact_known_secrets_with_explicit_columns(
    fake_aap: Any, command: str, fmt: str, field: str
) -> None:
    seed(fake_aap, "job_templates")
    fake_aap.store["job_templates"][10].update(
        webhook_key="synthetic-read-secret",
        survey_spec={"spec": [{"type": "password", "default": "synthetic-nested-secret"}]},
    )
    result = CliInvoker().invoke(
        app, ["job-templates", command, "10", "--by-id", "--columns", field, "--format", fmt]
    )
    assert result.exit_code == 0, result.output
    assert "synthetic-read-secret" not in result.output
    assert "synthetic-nested-secret" not in result.output
    assert "redacted" in result.stdout
    assert fake_aap.get_record("job_templates", 10)["webhook_key"] == "synthetic-read-secret"


def test_get_redacts_full_readonly_credential_record(fake_aap: Any) -> None:
    fake_aap.seed("credentials", id=10, name="machine", inputs={"password": "credential-secret"})
    result = CliInvoker().invoke(app, ["credentials", "get", "10", "--by-id", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "credential-secret" not in result.output
    assert json.loads(result.stdout)[0]["inputs"]["password"] == "<redacted>"


@pytest.mark.parametrize("summary", [False, True])
@pytest.mark.parametrize("verb", ["add", "remove"])
def test_membership_numeric_ancestry_preflights_entire_batch(
    fake_aap: Any, summary: bool, verb: str
) -> None:
    fake_aap.seed("organizations", id=1, name="one")
    fake_aap.seed("organizations", id=7, name="two")
    fake_aap.seed("inventories", id=2, name="prod", organization=1)
    fake_aap.seed("inventories", id=3, name="prod", organization=7)
    fields = {"summary_fields": {"inventory": {"id": 2, "name": "prod"}}} if summary else {}
    fake_aap.seed("groups", id=20, name="group", inventory=2, **fields)
    fake_aap.seed("hosts", id=30, name="valid", inventory=2)
    fake_aap.seed("hosts", id=31, name="invalid", inventory=3)
    fake_aap.memberships[("groups", 20, "hosts")] = {30, 31} if verb == "remove" else set()
    before = set(fake_aap.memberships[("groups", 20, "hosts")])
    result = CliInvoker().invoke(
        app, ["groups", "hosts", verb, "20", "30", "31", "--by-id", "--inventory", "prod", "--yes"]
    )
    assert result.exit_code != 0, result.output
    assert not any(call.request.method == "POST" for call in fake_aap.router.calls)
    assert fake_aap.memberships[("groups", 20, "hosts")] == before


@pytest.mark.parametrize("selector", ["names", "query"])
def test_membership_fetches_missing_inventory_organization_for_duplicate_names(
    fake_aap: Any, selector: str
) -> None:
    fake_aap.seed("organizations", id=1, name="one")
    fake_aap.seed("organizations", id=7, name="two")
    fake_aap.seed("inventories", id=2, name="prod", organization=1)
    fake_aap.seed("inventories", id=3, name="prod", organization=7)
    fake_aap.seed(
        "groups", id=20, name="group", inventory=2, summary_fields={"inventory": {"name": "prod"}}
    )
    fake_aap.seed("hosts", id=30, name="member", inventory=2)
    fake_aap.seed("hosts", id=31, name="member", inventory=3)
    selection = ["member"] if selector == "names" else ["--filter", "name=member"]
    result = CliInvoker().invoke(app, ["groups", "hosts", "add", "group", *selection, "--yes"])
    assert result.exit_code == 0, result.output
    assert fake_aap.memberships[("groups", 20, "hosts")] == {30}


@pytest.mark.parametrize("relation", [None, 999, 2])
def test_membership_fails_closed_when_required_ancestry_is_missing(
    fake_aap: Any, relation: int | None
) -> None:
    # Inventory 2 deliberately has no organization ancestry.
    fake_aap.seed("inventories", id=2, name="prod")
    fake_aap.seed("groups", id=20, name="group", inventory=relation)
    fake_aap.seed("hosts", id=30, name="member", inventory=2)
    result = CliInvoker().invoke(app, ["groups", "hosts", "add", "20", "30", "--by-id", "--yes"])
    assert result.exit_code != 0, result.output
    assert not any(call.request.method == "POST" for call in fake_aap.router.calls)


def test_membership_organization_scope_uses_numeric_parent_relation(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="one")
    fake_aap.seed("organizations", id=2, name="two")
    fake_aap.seed("job_templates", id=20, name="template", organization=1)
    fake_aap.seed("credentials", id=30, name="valid", organization=1)
    fake_aap.seed("credentials", id=31, name="invalid", organization=2)
    result = CliInvoker().invoke(
        app, ["job-templates", "credentials", "add", "20", "30", "31", "--by-id", "--yes"]
    )
    assert result.exit_code != 0, result.output
    assert not any(call.request.method == "POST" for call in fake_aap.router.calls)
