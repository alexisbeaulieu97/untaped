"""AWX editor batches use private files and the shared fixed-target mutation gate."""

import builtins
import io
import json
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.awx.integration.test_selection_mutation_cli import KINDS, pipe, seed
from untaped.capabilities.awx.cli.commands import app
from untaped.testing import CliInvoker, ScriptedPromptBackend


@pytest.fixture
def editor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., list[Path]]:
    """Replace only external process/terminal I/O; exercise the real HTTP-backed CLI."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    real_open = builtins.open

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    def open_terminal(file: Any, *args: Any, **kwargs: Any) -> Any:
        if file == "/dev/tty":
            return Terminal()
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_terminal)
    monkeypatch.setenv("VISUAL", "test-editor --wait")

    def install(*edits: Any) -> list[Path]:
        paths: list[Path] = []
        actions = iter(edits)

        def run(argv: list[str], **kwargs: Any) -> None:
            path = Path(argv[-1])
            paths.append(path)
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            assert kwargs["stdin"].isatty()
            assert kwargs["stdout"] is kwargs["stdin"]
            assert kwargs["stderr"] is kwargs["stdin"]
            action = next(actions)
            if isinstance(action, BaseException):
                raise action
            try:
                documents = list(yaml.safe_load_all(path.read_text()))
            except yaml.YAMLError:
                documents = []
            updated = action(documents)
            if isinstance(updated, str):
                path.write_text(updated)
            else:
                # Real editors commonly replace the original inode.
                replacement = path.with_suffix(".new")
                replacement.write_text(yaml.safe_dump_all(updated, sort_keys=False))
                replacement.chmod(0o644)
                replacement.replace(path)

        monkeypatch.setattr(subprocess, "run", run)
        return paths

    return install


def changed(documents: list[Any]) -> list[Any]:
    for doc in documents:
        doc["spec"]["description"] = "new"
    return documents


@pytest.mark.parametrize(("cli", "path"), KINDS)
def test_edit_all_writable_kinds(fake_aap: Any, editor: Any, cli: str, path: str) -> None:
    seed(fake_aap, path)
    paths = editor(changed)
    backend = ScriptedPromptBackend(confirms=[True])
    result = CliInvoker().invoke(
        app,
        [cli, "edit", "10", "--by-id", "--field", "description", "--format", "json"],
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output
    assert fake_aap.get_record(path, 10)["description"] == "new"
    assert json.loads(result.stdout)[0]["action"] == "updated"
    assert len(backend.calls) == 1
    assert not paths[0].parent.exists()


@pytest.mark.parametrize("cli", ["organizations", "credentials", "credential-types"])
def test_readonly_excludes_edit(cli: str) -> None:
    result = CliInvoker().invoke(app, [cli, "edit", "target"])
    assert result.exit_code != 0


def test_editor_noop_cleans_private_directory_without_prompt(fake_aap: Any, editor: Any) -> None:
    seed(fake_aap, "projects")
    paths = editor(lambda docs: docs)
    result = CliInvoker().invoke(app, ["projects", "edit", "target", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "unchanged"
    assert not paths[0].parent.exists()
    assert not any(call.request.method == "PATCH" for call in fake_aap.router.calls)


@pytest.mark.parametrize(
    "change", ["yaml", "duplicate", "new", "retarget", "identity", "extra", "kind"]
)
def test_invalid_edit_retains_owner_only_file_without_writes(
    fake_aap: Any, editor: Any, change: str
) -> None:
    seed(fake_aap, "projects")

    def invalid(docs: list[Any]) -> Any:
        if change == "yaml":
            return "spec: [secret-new-value\n"
        if change == "duplicate":
            return docs + docs
        if change == "new":
            docs[0]["identity"]["id"] = 999
        if change == "retarget":
            docs[0]["identity"]["id"] = 3
        if change == "identity":
            docs[0]["identity"]["metadata"]["name"] = "renamed"
        if change == "kind":
            docs[0]["identity"]["kind"] = "Host"
        if change == "extra":
            docs[0]["spec"]["scm_branch"] = "secret-new-value"
        return docs

    paths = editor(invalid)
    result = CliInvoker().invoke(
        app,
        ["projects", "edit", "target", "--field", "description", "--yes"],
        prompt_backend=ScriptedPromptBackend(confirms=[False]),
    )
    assert result.exit_code != 0, result.output
    assert fake_aap.get_record("projects", 10)["description"] == "old"
    assert str(paths[0]) in result.stderr
    assert paths[0].exists()
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    assert "secret-new-value" not in result.output


def test_invalid_yaml_reopen_then_confirm_with_piped_selection(fake_aap: Any, editor: Any) -> None:
    seed(fake_aap, "projects")
    original: list[Any] = []

    def invalid(docs: list[Any]) -> str:
        original.extend(docs)
        return "[invalid"

    # Second launch ignores the invalid contents and repairs using original snapshot.
    paths = editor(invalid, lambda _: changed(original))
    backend = ScriptedPromptBackend(confirms=[True, True])
    result = CliInvoker().invoke(
        app,
        ["projects", "edit", "--stdin", "--format", "json"],
        input=pipe("awx.project", 10),
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output
    assert len(paths) == 2 and paths[0] == paths[1]
    assert len(backend.calls) == 2
    assert fake_aap.get_record("projects", 10)["description"] == "new"


def test_editor_failure_retains_file(fake_aap: Any, editor: Any) -> None:
    seed(fake_aap, "projects")
    paths = editor(subprocess.CalledProcessError(7, "test-editor"))
    result = CliInvoker().invoke(app, ["projects", "edit", "target", "--yes"])
    assert result.exit_code != 0
    assert str(paths[0]) in result.stderr
    assert paths[0].exists()
    assert fake_aap.get_record("projects", 10)["description"] == "old"


def test_editor_always_requires_terminal(fake_aap: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(fake_aap, "projects")
    original = builtins.open

    def no_tty(file: Any, *args: Any, **kwargs: Any) -> Any:
        if file == "/dev/tty":
            raise OSError("no controlling terminal")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_tty)
    result = CliInvoker().invoke(app, ["projects", "edit", "target", "--yes"])
    assert result.exit_code != 0
    assert "terminal" in result.output
    assert fake_aap.get_record("projects", 10)["description"] == "old"


@pytest.mark.parametrize("command", ["edit", "patch", "apply"])
def test_noop_batch_with_secret_and_existing_membership_never_prompts(
    fake_aap: Any, editor: Any, tmp_path: Path, command: str
) -> None:
    seed(fake_aap, "job_templates")
    fake_aap.store["job_templates"][10]["webhook_key"] = "existing-secret"
    fake_aap.seed("credentials", id=30, name="machine", organization=1)
    fake_aap.memberships[("job_templates", 10, "credentials")] = {30}
    paths = editor(lambda docs: docs)
    if command == "apply":
        file = tmp_path / "job.yml"
        file.write_text(
            "kind: JobTemplate\nmetadata: {name: target, organization: Default}\n"
            "spec: {description: old, webhook_key: '$encrypted$', credentials: [machine]}\n"
        )
        args = [str(file)]
    elif command == "patch":
        args = [
            "target",
            "--set",
            "description=old",
            "--set",
            "webhook_key=$encrypted$",
            "--set",
            'credentials=["machine"]',
        ]
    else:
        args = ["target"]
    backend = ScriptedPromptBackend(confirms=[])
    result = CliInvoker().invoke(
        app, ["job-templates", command, *args, "--format", "json"], prompt_backend=backend
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "unchanged"
    assert not backend.calls
    assert not any(call.request.method in {"PATCH", "POST"} for call in fake_aap.router.calls)
    assert "existing-secret" not in result.output
    if paths:
        assert not paths[0].parent.exists()


def test_retarget_to_same_named_selected_id_is_invalid(fake_aap: Any, editor: Any) -> None:
    seed(fake_aap, "projects")
    fake_aap.seed("projects", id=11, name="target", organization=1, description="old")

    def retarget(docs: list[Any]) -> list[Any]:
        docs[0]["identity"]["id"] = 11
        return changed(docs[:1])

    editor(retarget)
    result = CliInvoker().invoke(
        app,
        ["projects", "edit", "10", "11", "--by-id", "--yes"],
        prompt_backend=ScriptedPromptBackend(confirms=[False]),
    )
    assert result.exit_code != 0, result.output
    assert not any(call.request.method == "PATCH" for call in fake_aap.router.calls)


def test_membership_changed_while_editor_open_conflicts_before_any_write(
    fake_aap: Any, editor: Any
) -> None:
    seed(fake_aap, "job_templates")
    for id_, name in [(30, "first"), (31, "second"), (32, "added-remotely")]:
        fake_aap.seed("credentials", id=id_, name=name, organization=1)
    fake_aap.memberships[("job_templates", 10, "credentials")] = {30, 31}

    def update(docs: list[Any]) -> list[Any]:
        fake_aap.memberships[("job_templates", 10, "credentials")] = {30, 31, 32}
        docs[0]["spec"]["credentials"] = ["second"]
        return changed(docs)

    editor(update)
    result = CliInvoker().invoke(
        app, ["job-templates", "edit", "target", "--yes", "--format", "json"]
    )
    assert result.exit_code != 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "conflict"
    assert fake_aap.memberships[("job_templates", 10, "credentials")] == {30, 31, 32}
    assert not any(call.request.method in {"PATCH", "POST"} for call in fake_aap.router.calls)


def test_original_duplicate_fk_labels_keep_ids_when_other_fields_change(
    fake_aap: Any, editor: Any
) -> None:
    seed(fake_aap, "job_templates")
    for id_ in (30, 31):
        fake_aap.seed("credentials", id=id_, name="same", organization=1)
    for id_ in (40, 41):
        fake_aap.seed("credentials", id=id_, name="123", organization=1)
    fake_aap.store["job_templates"][10]["webhook_credential"] = 41
    fake_aap.memberships[("job_templates", 10, "credentials")] = {30, 31}
    editor(changed)
    result = CliInvoker().invoke(app, ["job-templates", "edit", "target", "--yes"])
    assert result.exit_code == 0, result.output
    assert fake_aap.get_record("job_templates", 10)["webhook_credential"] == 41
    assert fake_aap.memberships[("job_templates", 10, "credentials")] == {30, 31}
    assert fake_aap.get_record("job_templates", 10)["description"] == "new"


def test_deselection_missing_field_and_nested_replacement(fake_aap: Any, editor: Any) -> None:
    seed(fake_aap, "job_templates")
    fake_aap.store["job_templates"][10]["extra_vars"] = '{"keep": 1, "remove": 2}'
    fake_aap.seed("job_templates", id=11, name="second", organization=1, description="old")

    def update(docs: list[Any]) -> list[Any]:
        docs[0]["spec"].pop("description")
        docs[0]["spec"]["extra_vars"] = {"keep": 1}
        return docs[:1]

    editor(update)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "edit",
            "10",
            "11",
            "--by-id",
            "--field",
            "extra_vars",
            "--field",
            "description",
            "--yes",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.stdout)] == [10]
    assert fake_aap.get_record("job_templates", 10)["description"] == "old"
    assert json.loads(fake_aap.get_record("job_templates", 10)["extra_vars"]) == {"keep": 1}
    assert fake_aap.get_record("job_templates", 11)["description"] == "old"


@pytest.mark.parametrize("value", ["new-environment", 42])
def test_changed_fk_value_uses_shared_resolution(fake_aap: Any, editor: Any, value: Any) -> None:
    seed(fake_aap, "job_templates")
    fake_aap.seed("credentials", id=41, name="old-environment", organization=1)
    fake_aap.seed("credentials", id=42, name="new-environment", organization=1)
    fake_aap.store["job_templates"][10]["webhook_credential"] = 41

    def update(docs: list[Any]) -> list[Any]:
        docs[0]["spec"]["webhook_credential"] = value
        return docs

    editor(update)
    result = CliInvoker().invoke(app, ["job-templates", "edit", "target", "--yes"])
    assert result.exit_code == 0, result.output
    assert fake_aap.get_record("job_templates", 10)["webhook_credential"] == 42


@pytest.mark.parametrize("fmt", ["json", "yaml", "pipe", "table", "raw"])
def test_new_editor_secret_never_leaks(fake_aap: Any, editor: Any, fmt: str) -> None:
    seed(fake_aap, "job_templates")
    fake_aap.store["job_templates"][10]["webhook_key"] = "old-secret"

    def update(docs: list[Any]) -> list[Any]:
        assert docs[0]["spec"]["webhook_key"] == "$encrypted$"
        docs[0]["spec"]["webhook_key"] = "new-editor-secret"
        return docs

    editor(update)
    result = CliInvoker().invoke(app, ["job-templates", "edit", "target", "--yes", "--format", fmt])
    assert result.exit_code == 0, result.output
    assert fake_aap.get_record("job_templates", 10)["webhook_key"] == "new-editor-secret"
    assert "new-editor-secret" not in result.output and "old-secret" not in result.output


def test_reopen_uses_fresh_default_backend_stream_each_time(
    fake_aap: Any, editor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.prompts import PromptToolkitPromptBackend

    seed(fake_aap, "projects")
    original: list[Any] = []
    streams: list[Any] = []

    def invalid(docs: list[Any]) -> str:
        original.extend(docs)
        return "[invalid"

    def confirm(backend: Any, message: str, *, default: bool) -> bool:
        assert not backend.stdin.closed
        assert backend.stdin.isatty()
        assert default is False
        streams.append(backend.stdin)
        return True

    monkeypatch.setattr(PromptToolkitPromptBackend, "confirm", confirm)
    paths = editor(invalid, lambda _: "[invalid", lambda _: changed(original))
    result = CliInvoker().invoke(
        app, ["projects", "edit", "--stdin", "--format", "json"], input=pipe("awx.project", 10)
    )
    assert result.exit_code == 0, result.output
    assert len(streams) == 3 and len({id(stream) for stream in streams}) == 3
    assert all(stream.closed for stream in streams)
    assert len(paths) == 3
    assert not paths[0].parent.exists()
    assert fake_aap.get_record("projects", 10)["description"] == "new"


@pytest.mark.parametrize("mode", ["names", "query", "all", "bare", "typed"])
def test_editor_selection_modes_remain_fixed(fake_aap: Any, editor: Any, mode: str) -> None:
    seed(fake_aap, "projects")
    editor(changed)
    args = {
        "names": ["target"],
        "query": ["--filter", "id=10"],
        "all": ["--all", "--organization", "Default"],
        "bare": ["--stdin"],
        "typed": ["--stdin"],
    }[mode]
    result = CliInvoker().invoke(
        app,
        ["projects", "edit", *args, "--field", "description", "--yes"],
        input=pipe("awx.project", 10)
        if mode == "typed"
        else ("target\n" if mode == "bare" else None),
    )
    assert result.exit_code == 0, result.output
    assert fake_aap.get_record("projects", 10)["description"] == "new"


@pytest.mark.parametrize("mode", ["cancel", "dry-run", "failure", "deselected"])
def test_editor_cleanup_or_retention_after_runner(
    fake_aap: Any, editor: Any, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    seed(fake_aap, "projects")
    paths = editor((lambda _: []) if mode == "deselected" else changed)
    if mode == "failure":
        monkeypatch.setattr(
            fake_aap,
            "_update",
            lambda *args: httpx.Response(500, json={"detail": "server failure"}),
        )
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "edit",
            "target",
            "--format",
            "json",
            *(["--dry-run"] if mode == "dry-run" else []),
        ],
        prompt_backend=ScriptedPromptBackend(confirms=[mode == "failure"]),
    )
    assert (result.exit_code == 0) is (mode != "failure"), result.output
    assert paths[0].exists() is (mode == "failure")
    assert fake_aap.get_record("projects", 10)["description"] == "old"
    if mode == "failure":
        assert str(paths[0]) in result.stderr


def test_editor_missing_configuration_is_clean_and_retained(
    fake_aap: Any, editor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(fake_aap, "projects")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    result = CliInvoker().invoke(app, ["projects", "edit", "target", "--yes"])
    assert result.exit_code != 0
    assert "VISUAL" in result.output and "retained" in result.output
    assert fake_aap.get_record("projects", 10)["description"] == "old"


@pytest.mark.parametrize(
    "extra",
    [
        ["--field", "name"],
        ["--field", "not_a_field"],
        ["--yes", "--dry-run"],
        ["--allow-unverified"],
        ["--inventory", "prod"],
        ["--parallel", "0"],
    ],
)
def test_editor_rejects_invalid_controls_before_launch(
    fake_aap: Any, editor: Any, extra: list[str]
) -> None:
    seed(fake_aap, "projects")
    paths = editor(changed)
    result = CliInvoker().invoke(app, ["projects", "edit", "target", *extra])
    assert result.exit_code != 0
    assert not paths
    assert fake_aap.get_record("projects", 10)["description"] == "old"


def test_invalid_fk_mapping_never_leaks_parser_values(fake_aap: Any, editor: Any) -> None:
    seed(fake_aap, "job_templates")

    def update(docs: list[Any]) -> list[Any]:
        docs[0]["spec"]["webhook_credential"] = {"invalid": "new-secret-in-invalid-input"}
        return docs

    paths = editor(update)
    result = CliInvoker().invoke(
        app,
        ["job-templates", "edit", "target", "--yes"],
        prompt_backend=ScriptedPromptBackend(confirms=[False]),
    )
    assert result.exit_code == 1, result.output
    assert "new-secret-in-invalid-input" not in result.output
    assert "validation cancelled" in result.output
    assert paths[0].exists()
    assert not any(call.request.method in {"PATCH", "POST"} for call in fake_aap.router.calls)
