"""``untaped recipe hooks``: listing, showing, scaffolding and running hooks."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from recipe_pack_support import install_pack, write_hook_project, write_pack
from recipe_test_support import library_dir, run, run_root
from untaped.sdk.testing import (
    ScriptedTerminal,
)
from untaped_recipe.builtins.registry import BUILTIN_HOOKS, BuiltinHook
from untaped_recipe.infrastructure.pack_store import PackLibrary


def _ctx(project: Path, target: Path) -> list[str]:
    """``hooks run`` options naming the hook project and target directory."""
    return ["--project", str(project), "--target", str(target)]


def test_hook_run_accepts_path_ref_form_like_new_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise S1: `hooks run ./pack/hook` must resolve like `hooks init` does.
    project = tmp_path / "collections-ensure"
    project.mkdir()
    write_hook_project(
        project,
        public_name="has_playbooks",
        module_name="has_playbooks",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )
    target = tmp_path / "app"
    target.mkdir()
    monkeypatch.chdir(tmp_path)

    ok = run(
        [
            "hooks",
            "run",
            "./collections-ensure/has_playbooks",
            "--target",
            str(target),
            "--format",
            "json",
        ],
    )
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["status"] == "pass"

    conflict = run(
        [
            "hooks",
            "run",
            "./collections-ensure/has_playbooks",
            "--project",
            str(project),
            "--target",
            str(target),
        ],
    )
    assert conflict.exit_code == 2
    assert "not both" in conflict.output


def test_hook_run_transform_reads_disk_and_emits_exact_content(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="append",
        module_name="append",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    print('external diagnostic')\n"
            "    return content + args['suffix']\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("start")

    result = run(
        [
            "hooks",
            "run",
            "append",
            *_ctx(hook_project, target),
            "--file",
            "local.txt",
            "--arg",
            "suffix='!'",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start!"
    assert (target / "local.txt").read_text() == "start"
    assert "external diagnostic" in result.stderr
    assert str(target) in result.stderr
    assert "local.txt" in result.stderr
    assert '"suffix": "!"' in result.stderr


def test_hook_run_transform_content_overrides_do_not_require_existing_file(
    tmp_path: Path,
) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="show_context",
        module_name="show_context",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + '|' + file.name + '|' + target.name\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    content_file = tmp_path / "fixture.txt"
    content_file.write_text("from-file")

    literal = run(
        [
            "hooks",
            "run",
            "show_context",
            *_ctx(hook_project, target),
            "--file",
            "missing.txt",
            "--content",
            "literal",
        ],
    )
    stdin = run(
        [
            "hooks",
            "run",
            "show_context",
            *_ctx(hook_project, target),
            "--file",
            "stdin.txt",
            "--content",
            "-",
        ],
        input="from-stdin",
    )
    file_result = run(
        [
            "hooks",
            "run",
            "show_context",
            *_ctx(hook_project, target),
            "--file",
            "file.txt",
            "--content-file",
            str(content_file),
        ],
    )

    assert literal.exit_code == 0, literal.output
    assert literal.stdout == "literal|missing.txt|target"
    assert stdin.exit_code == 0, stdin.output
    assert stdin.stdout == "from-stdin|stdin.txt|target"
    assert file_result.exit_code == 0, file_result.output
    assert file_result.stdout == "from-file|file.txt|target"


def test_hook_run_content_from_a_terminal_stdin_is_refused_not_read(
    tmp_path: Path, terminal: ScriptedTerminal
) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="show_context",
        module_name="show_context",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    terminal.answers()  # stdin is the terminal: nothing piped

    result = run(
        [
            "hooks",
            "run",
            "show_context",
            *_ctx(hook_project, target),
            "--file",
            "stdin.txt",
            "--content",
            "-",
        ],
    )

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "error: --content: - reads stdin; pipe it" in result.stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--content-file", "{missing}"], "error: --content-file file not found"),
        (["--arg", "items=["], "error: --arg value for 'items' is invalid YAML"),
    ],
)
def test_hook_run_bad_flag_values_are_reported_cleanly(
    tmp_path: Path, args: list[str], message: str
) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="types",
        module_name="types",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")
    extra = [arg.format(missing=tmp_path / "missing.txt") for arg in args]

    result = run(
        ["hooks", "run", "types", *_ctx(hook_project, target), "--file", "local.txt", *extra],
    )

    assert result.exit_code != 0
    assert message in result.output
    assert "Traceback" not in result.output


def test_hook_run_transform_diff_and_structured_output(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="replace",
        module_name="replace",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content.replace('old', 'new')\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("old\n")

    diff = run(
        ["hooks", "run", "replace", *_ctx(hook_project, target), "--file", "local.txt", "--diff"],
    )
    structured = run(
        [
            "hooks",
            "run",
            "replace",
            *_ctx(hook_project, target),
            "--file",
            "local.txt",
            "--diff",
            "--format",
            "json",
        ],
    )

    assert diff.exit_code == 0, diff.output
    assert "--- a/local.txt" in diff.stdout
    assert "+++ b/local.txt" in diff.stdout
    assert "-old" in diff.stdout
    assert "+new" in diff.stdout
    assert structured.exit_code == 0, structured.output
    row = json.loads(structured.stdout)
    assert row["content"] == "new\n"
    assert "--- a/local.txt" in row["diff"]
    assert "inputs" not in row
    assert "args" not in row


def test_hook_run_refuses_an_unknown_column_before_running_the_hook(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="ready",
        module_name="ready",
        code=(
            "import pathlib\n\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    pathlib.Path(str(target), 'ran').write_text('x')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    result = run(
        ["hooks", "run", "ready", *_ctx(hook_project, target), "-f", "json", "-c", "status,nope"],
    )

    assert result.exit_code == 2, result.output
    assert "unknown column 'nope'" in result.stderr
    assert not (target / "ran").exists()


def test_hook_run_validate_records_and_fail_exit(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="ready",
        module_name="ready",
        code=(
            "def validate(*, inputs, target, args, helpers):\n"
            "    if args.get('fail'):\n"
            "        return helpers.fail('not ready')\n"
            "    helpers.warn('check manually')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    warn = run(
        ["hooks", "run", "ready", *_ctx(hook_project, target), "--format", "json"],
    )
    failed = run(
        [
            "hooks",
            "run",
            "ready",
            *_ctx(hook_project, target),
            "--arg",
            "fail=yes",
            "--format",
            "pipe",
        ],
    )

    assert warn.exit_code == 0, warn.output
    warn_row = json.loads(warn.stdout)
    assert warn_row["hook"] == "ready"
    assert warn_row["kind"] == "validate"
    # helpers.warn() is now an accumulator: the verdict passes and the
    # warning surfaces on the human channel (stderr).
    assert warn_row["status"] == "pass"
    assert warn_row["message"] == ""
    assert "check manually" in warn.stderr
    assert failed.exit_code == 1, failed.output
    failed_row = json.loads(failed.stdout)
    assert failed_row["kind"] == "recipe.hook_run"
    assert failed_row["record"]["status"] == "fail"
    assert failed_row["record"]["message"] == "not ready"


def test_hook_run_dual_export_infers_or_requires_kind(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="dual",
        module_name="dual",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return 'transformed'\n\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    helpers.warn('validated')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("before")

    inferred_transform = run(
        ["hooks", "run", "dual", *_ctx(hook_project, target), "--file", "local.txt"],
    )
    ambiguous = run(
        ["hooks", "run", "dual", *_ctx(hook_project, target)],
    )
    explicit_validate = run(
        [
            "hooks",
            "run",
            "dual",
            *_ctx(hook_project, target),
            "--kind",
            "validate",
            "--format",
            "json",
        ],
    )

    assert inferred_transform.exit_code == 0, inferred_transform.output
    assert inferred_transform.stdout == "transformed"
    assert ambiguous.exit_code != 0
    assert (
        "hook 'dual' exports both transform() and validate(); pass --kind or --file"
        in ambiguous.output
    )
    assert explicit_validate.exit_code == 0, explicit_validate.output
    row = json.loads(explicit_validate.stdout)
    assert row["kind"] == "validate"
    assert row["status"] == "pass"
    assert row["message"] == ""
    assert "validated" in explicit_validate.stderr


def test_hook_run_rejects_kind_specific_context_options(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    marker = tmp_path / "marker"
    write_hook_project(
        hook_project,
        public_name="ready",
        module_name="ready",
        code=(
            "from pathlib import Path\n"
            "def validate(*, inputs, target, args, helpers):\n"
            f"    Path({str(marker)!r}).write_text('ran')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    validate_with_file = run(
        ["hooks", "run", "ready", *_ctx(hook_project, target), "--file", "local.txt"],
    )
    validate_with_diff = run(
        ["hooks", "run", "ready", *_ctx(hook_project, target), "--diff"],
    )
    transform_without_file = run(
        [
            "hooks",
            "run",
            "yaml_edit",
            "--target",
            str(target),
        ],
    )

    assert validate_with_file.exit_code != 0
    assert "validate hooks do not accept --file or content options" in validate_with_file.output
    assert validate_with_diff.exit_code != 0
    assert "validate hooks do not accept --file or content options" in validate_with_diff.output
    assert not marker.exists()
    assert transform_without_file.exit_code != 0
    assert "transform hooks require --file" in transform_without_file.output


def test_hook_run_inputs_and_args_merge_files_and_yaml_flags(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="types",
        module_name="types",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return (\n"
            "        f\"enabled={inputs['enabled']!r};\"\n"
            "        f\"count={inputs['count']!r};\"\n"
            "        f\"mode={args['mode']!r}\"\n"
            "    )\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")
    inputs = tmp_path / "inputs.yml"
    inputs.write_text("enabled: false\ncount: 1\n")
    args = tmp_path / "args.yml"
    args.write_text("mode: old\n")

    result = run(
        [
            "hooks",
            "run",
            "types",
            *_ctx(hook_project, target),
            "--file",
            "local.txt",
            "--vars-file",
            str(inputs),
            "--var",
            "enabled=yes",
            "--var",
            "count=3",
            "--args-file",
            str(args),
            "--arg",
            "mode=new",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "enabled=True;count=3;mode='new'"
    assert '"enabled": true' in result.stderr
    assert '"count": 3' in result.stderr
    assert '"mode": "new"' in result.stderr


def test_hook_run_explicit_project_must_be_valid_before_global_or_builtin_fallback(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("enabled: false\n")
    args = tmp_path / "args.yml"
    args.write_text("edits:\n  - {op: set, path: [enabled], value: true}\n")

    result = run(
        [
            "hooks",
            "run",
            "yaml_edit",
            "--project",
            str(tmp_path / "missing-project"),
            "--target",
            str(target),
            "--file",
            "local.yml",
            "--args-file",
            str(args),
        ],
    )

    assert result.exit_code != 0
    assert "error: hook project not found" in result.output
    assert result.stdout == ""
    assert "Hook run:" not in result.stderr


def test_hook_run_never_adopts_cwd_project_without_explicit_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_project = tmp_path / "pack-source"
    write_hook_project(
        global_project,
        public_name="shadow",
        module_name="shadow",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return 'global'\n"
        ),
    )
    PackLibrary(library_dir=library_dir()).add(
        global_project,
        source=str(global_project),
        rev=None,
        name="shared",
        force=False,
    )
    cwd_project = tmp_path / "cwd"
    write_hook_project(
        cwd_project,
        public_name="shadow",
        module_name="shadow",
        code="def transform(content, *, inputs, target, file, args, helpers):\n    return 'cwd'\n",
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")

    monkeypatch.chdir(cwd_project)
    implicit = run(
        ["hooks", "run", "shadow", "--target", str(target), "--file", "local.txt"],
    )
    explicit = run(
        [
            "hooks",
            "run",
            "shadow",
            "--project",
            ".",
            "--target",
            str(target),
            "--file",
            "local.txt",
        ],
    )
    path_ref = run(
        ["hooks", "run", "../cwd/shadow", "--target", str(target), "--file", "local.txt"],
    )

    # The cwd's hook project is only used when named explicitly.
    assert implicit.exit_code == 0, implicit.output
    assert implicit.stdout == "global"
    assert explicit.exit_code == 0, explicit.output
    assert explicit.stdout == "cwd"
    assert path_ref.exit_code == 0, path_ref.output
    assert path_ref.stdout == "cwd"


def test_hook_run_builtin_is_not_shadowed_by_cwd_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd_project = tmp_path / "cloned"
    write_hook_project(
        cwd_project,
        public_name="yaml_edit",
        module_name="evil",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return 'repo code ran'\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "a.yml").write_text("a: 1\n")
    monkeypatch.chdir(cwd_project)

    result = run(
        [
            "hooks",
            "run",
            "yaml_edit",
            "--target",
            str(target),
            "--file",
            "a.yml",
            "--arg",
            "edits=[{op: set, path: [a], value: 2}]",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "a: 2\n"


def test_hook_run_external_failure_prints_traceback(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="broken",
        module_name="broken",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    print('before failure')\n"
            "    raise RuntimeError('boom')\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")

    result = run(
        ["hooks", "run", "broken", *_ctx(hook_project, target), "--file", "local.txt"],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "before failure" in result.stderr
    assert "Traceback" in result.stderr
    assert "RuntimeError: boom" in result.stderr


def test_hook_run_builtin_stdout_is_redirected_to_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("debug_builtin")

    def transform(content: str, **kwargs: object) -> str:
        print("builtin diagnostic")
        return content + "!"

    module.transform = transform  # type: ignore[attr-defined]
    monkeypatch.setitem(
        BUILTIN_HOOKS,
        "debug_builtin",
        BuiltinHook(module=module, exports=frozenset({"transform"})),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("start")

    result = run(
        ["hooks", "run", "debug_builtin", "--target", str(target), "--file", "local.txt"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start!"
    assert "builtin diagnostic" in result.stderr


def test_hook_run_quiet_suppresses_context_but_not_hook_diagnostics(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    write_hook_project(
        hook_project,
        public_name="noisy",
        module_name="noisy",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    print('hook diagnostic')\n"
            "    return content\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("start")

    result = run_root(
        [
            "--quiet",
            "recipe",
            "hooks",
            "run",
            "noisy",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start"
    assert "hook diagnostic" in result.stderr
    assert "Hook run:" not in result.stderr
    assert str(target) not in result.stderr


def test_show_prefers_library_hook_over_builtin(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_pack(
        source,
        manifest_name="shadow",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"yaml_edit": "shadow_pack.hooks.yaml_edit"},
    )
    install_pack(source)

    result = run(["hooks", "get", "yaml_edit", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["module"] == "shadow_pack.hooks.yaml_edit"


def test_get_hook_reports_an_ambiguous_library_hook_instead_of_the_builtin(
    tmp_path: Path,
) -> None:
    for name in ("one", "two"):
        source = tmp_path / name
        write_pack(
            source,
            manifest_name=name,
            recipes={"playbook": "recipes/playbook.yml"},
            hooks={"yaml_edit": f"{name}_pack.hooks.yaml_edit"},
        )
        install_pack(source)

    result = run(["hooks", "get", "yaml_edit", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert "ambiguous hook ref 'yaml_edit'" in result.stderr


def test_edit_rejects_builtin_hook(tmp_path: Path) -> None:
    result = run(["hooks", "edit", "yaml_edit"])

    assert result.exit_code == 1
    assert "built-in hooks are engine-owned and cannot be edited: yaml_edit" in result.stderr


def test_an_unknown_column_on_recipe_records_is_a_usage_error(tmp_path: Path) -> None:
    result = run(["hooks", "list", "--format", "json", "--columns", "nope"])

    assert result.exit_code == 2
    assert "unknown column 'nope'" in result.stderr


def test_list_hooks_shows_builtins_even_on_empty_library(tmp_path: Path) -> None:
    result = run(["hooks", "list", "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "pack": "(builtin)",
            "name": "yaml_edit",
            "ref": "yaml_edit",
            "module": "untaped_recipe.builtins.hooks.yaml_edit",
            "exports": None,
            "path": rows[0]["path"],
        }
    ]
    assert rows[0]["path"].endswith("yaml_edit.py")
    assert "no packs installed" not in result.stderr


def test_builtin_hook_get_and_validate_render_detail_and_pass_row(tmp_path: Path) -> None:
    shown = run(["hooks", "get", "yaml_edit", "--format", "json"])
    checked = run(["validate", "yaml_edit", "--format", "json"])

    assert shown.exit_code == 0, shown.output
    detail = json.loads(shown.stdout)
    assert detail["ref"] == "yaml_edit"
    assert detail["module"] == "untaped_recipe.builtins.hooks.yaml_edit"
    assert "transform" in detail["exports"]
    assert checked.exit_code == 0, checked.output
    rows = json.loads(checked.stdout)
    assert rows == [
        {
            "name": "yaml_edit",
            "type": "hook",
            "status": "pass",
            "path": rows[0]["path"],
            "detail": None,
        }
    ]
    assert rows[0]["path"].endswith("yaml_edit.py")


def test_a_successful_hook_that_prints_error_still_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = ModuleType("chatty")

    def transform(content: str, **_kwargs: object) -> str:
        print("error: only the hook's own log line")
        return content + "!"

    module.transform = transform  # type: ignore[attr-defined]
    hook = BuiltinHook(module=module, exports=frozenset({"transform"}))
    monkeypatch.setitem(BUILTIN_HOOKS, "chatty", hook)
    (tmp_path / "local.txt").write_text("start", encoding="utf-8")

    result = run(["hooks", "run", "chatty", "--target", str(tmp_path), "--file", "local.txt"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "start!"
    assert "error: only the hook's own log line" in result.stderr
