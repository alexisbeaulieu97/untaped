"""``untaped recipe apply``: planning, previews, confirmation, inputs, piped targets and writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import untaped_recipe.infrastructure.file_writer as file_writer_module
from recipe_pack_support import default_columns, install_pack, write_hook_project, write_pack
from recipe_test_support import library_dir, run, run_root
from untaped.sdk.testing import (
    ScriptedTerminal,
)
from untaped_recipe.infrastructure.backup import BackupStore

_OUT_RECIPE = (
    "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
)


def _out_recipe(root: Path, template: str = "hello\n") -> tuple[Path, Path]:
    """Write ``recipe.yml`` rendering ``template.txt`` to ``out.txt`` plus a ``target`` dir."""
    (root / "recipe.yml").write_text(_OUT_RECIPE)
    (root / "template.txt").write_text(template)
    target = root / "target"
    target.mkdir()
    return root / "recipe.yml", target


def test_apply_yes_writes_and_emits_json_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("service={{ service }}\n")
    target = tmp_path / "target"
    target.mkdir()

    result = run(
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "service=api",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "service=api\n"
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "applied"
    assert "Recipe preview:" in result.stderr
    assert str(target / "out.txt") in result.stderr
    assert "Recipe apply:" in result.stderr


def test_apply_dry_run_defaults_to_table_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe, target = _out_recipe(tmp_path)

    result = run(
        ["apply", str(recipe), str(target), "--dry-run", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "planned"
    assert "Recipe preview:" in result.stderr
    assert "path" in result.stderr
    assert "action" in result.stderr
    assert "changes" in result.stderr
    assert "files_changed" not in result.stderr
    assert "error" not in result.stderr
    assert str(target / "out.txt") in result.stderr
    assert "create" in result.stderr
    assert "+1 -0" in result.stderr
    assert "--- a/out.txt" not in result.stderr
    assert "+++ b/out.txt" not in result.stderr


def test_apply_table_draws_each_action_in_its_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    recipe, target = _out_recipe(tmp_path)

    result = run(["apply", str(recipe), str(target), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "\x1b[33mplanned" in result.stdout  # a warning, as core's outcome words say


def test_apply_table_preview_counts_prefix_like_diff_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: remove\n"
        "    file: doc.yml\n"
        "  - type: template\n"
        "    template: plus.txt\n"
        "    dest: plus.txt\n"
    )
    (tmp_path / "plus.txt").write_text("++same\n++same\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "doc.yml").write_text("---\nold\n")

    result = run(
        ["apply", str(recipe), str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert str(target / "doc.yml") in result.stderr
    assert "+0 -2" in result.stderr
    assert str(target / "plus.txt") in result.stderr
    assert "+2 -0" in result.stderr


def test_apply_preview_diff_preserves_patch_headers(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path, "hello")

    result = run(
        ["apply", str(recipe), str(target), "--dry-run", "--preview", "diff"],
    )

    assert result.exit_code == 0, result.output
    assert "Recipe preview:" in result.stderr
    assert f"# {target}" in result.stderr
    # A created file diffs from /dev/null, and a missing final newline is
    # marked so the output applies with `patch`/`git apply`.
    assert "--- /dev/null\n+++ b/out.txt\n" in result.stderr
    assert "+hello\n\\ No newline at end of file\n" in result.stderr
    assert str(target / "out.txt") not in result.stderr


@pytest.mark.parametrize("preview", ["table", "diff"])
@pytest.mark.parametrize("case", ["change", "sensitive", "error"])
def test_apply_preview_renders_each_row_kind_with_absolute_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preview: str,
    case: str,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.chdir(tmp_path)
    recipes = {
        "change": _OUT_RECIPE,
        "sensitive": _OUT_RECIPE.replace(
            "steps:", "inputs:\n  token: {type: str, sensitive: true, required: true}\nsteps:"
        ),
        "error": "version: 1\nsteps:\n  - type: validate\n    hook: noop\n",
    }
    Path("recipe.yml").write_text(recipes[case])
    Path("template.txt").write_text("token={{ token }}\n" if case == "sensitive" else "hello\n")
    Path("target").mkdir()
    extra = ["--var", "token=secret"] if case == "sensitive" else []

    result = run(
        [
            "apply",
            "./recipe.yml",
            "target",
            "--dry-run",
            "--preview",
            preview,
            "-f",
            "json",
            *extra,
        ],
    )

    target = tmp_path / "target"
    assert result.exit_code == (1 if case == "error" else 0), result.output
    assert json.loads(result.stdout)[0]["action"] == ("failed" if case == "error" else "planned")
    assert "Recipe preview:" in result.said
    assert str(target) in result.said
    if case == "change" and preview == "table":
        assert str(target / "out.txt") in result.said
        assert "+1 -0" in result.said
    elif case == "change":
        assert f"# {target}" in result.said
        assert "# target" not in result.said
        assert "--- /dev/null\n+++ b/out.txt\n" in result.said
    elif case == "sensitive":
        # Sensitive targets collapse to a files_changed row in every preview mode.
        assert "files_changed" in result.said
        assert "out.txt" not in result.said
        assert "secret" not in result.said
    else:
        assert "noop" in result.said
        assert "+++ b/" not in result.said


def test_apply_preview_none_keeps_summary_and_stdout_format_independent(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path)

    result = run(
        [
            *("apply", str(recipe), str(target), "--dry-run", "--preview", "none"),
            *("--format", "json", "--columns", "target_path"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"target_path": str(target)}]
    assert "Recipe preview:" in result.stderr
    assert "out.txt" not in result.stderr


def test_apply_quiet_keeps_preview_summary_and_mutes_post_run_info(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path)

    result = run_root(
        ["--quiet", "recipe", "apply", str(recipe), str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    # The preview, summary line included, is not commentary; the result summary is.
    assert "Recipe preview:" in result.stderr
    assert "out.txt" in result.stderr
    assert "Recipe dry run:" not in result.stderr


def test_apply_table_preview_uses_configured_collection_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.setenv("UNTAPED_UNTAPED__UI__COLLECTION_VIEW", "list")
    recipe, target = _out_recipe(tmp_path)

    result = run(
        ["apply", str(recipe), str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "path:" in result.stderr
    assert "action: create" in result.stderr
    assert str(target / "out.txt") in result.stderr


def test_apply_table_preview_uses_configured_preview_max_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "500")
    monkeypatch.setenv("UNTAPED_RECIPE__PREVIEW_MAX_ROWS", "1")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (tmp_path / "one.txt").write_text("one\n")
    (tmp_path / "two.txt").write_text("two\n")
    target = tmp_path / "target"
    target.mkdir()

    result = run(["apply", str(recipe), str(target), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert str(target) in result.stderr
    assert "files" in result.stderr
    assert "one.txt" not in result.stderr
    assert "two.txt" not in result.stderr


def test_apply_decline_reprints_summary_adjacent_to_prompt_without_writing(
    tmp_path: Path, terminal: ScriptedTerminal
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (tmp_path / "one.txt").write_text("one\n")
    (tmp_path / "two.txt").write_text("two\n")
    target = tmp_path / "target"
    target.mkdir()
    terminal.answers(False)

    result = run(["apply", str(recipe), str(target), "--preview", "table", "--format", "json"])

    # A declined confirmation exits 1 with the standard decline line.
    assert result.exit_code == 1, result.output
    assert terminal.asked == [("confirm", "Apply to 1 target?")]
    assert result.stdout == ""
    assert result.said.rstrip().endswith("cancelled; no changes made")
    before_decline = result.said.rsplit("cancelled; no changes made", maxsplit=1)[0]
    assert before_decline.rstrip().endswith(
        "Recipe preview: 1 target, 1 changing, 0 unchanged, 0 failed, 2 files changed"
    )
    assert before_decline.count("Recipe preview:") == 2
    assert not (target / "one.txt").exists()
    assert not (target / "two.txt").exists()


def test_apply_preview_only_modes_and_noninteractive_default_write_nothing(
    tmp_path: Path,
) -> None:
    recipe, target = _out_recipe(tmp_path)

    dry_run = run(["apply", str(recipe), str(target), "--dry-run"])
    check = run(["apply", str(recipe), str(target), "--check"])
    declined = run(["apply", str(recipe), str(target)])

    assert dry_run.exit_code == 0, dry_run.output
    assert check.exit_code == 3, check.output
    assert "cancelled; no changes made" not in dry_run.stderr + check.stderr
    assert declined.exit_code != 0
    assert "requires --yes" in declined.output
    assert not (target / "out.txt").exists()


@pytest.mark.parametrize("rollback_fails", [True, False])
def test_apply_keeps_backup_only_when_write_rollback_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rollback_fails: bool,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (recipe_dir / "one.txt").write_text("one-after\n")
    (recipe_dir / "two.txt").write_text("two-after\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "one.txt").write_text("one-before\n")
    (target / "two.txt").write_text("two-before\n")
    original_replace = file_writer_module.os.replace

    def fail_second_write(source: Path, dest: Path) -> None:
        if rollback_fails and ".rollback." in Path(source).name:
            raise OSError("rollback denied")
        if Path(dest).name == "two.txt":
            raise OSError("write failed")
        original_replace(source, dest)

    monkeypatch.setattr(file_writer_module.os, "replace", fail_second_write)

    result = run(
        ["apply", str(recipe_dir / "recipe.yml"), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0, result.output
    store = BackupStore(library_dir() / "backups")
    if not rollback_fails:
        assert store.list() == []
        return
    # The partially-written target keeps its backup so it can be restored.
    assert "rollback incomplete" in result.stdout
    assert len(store.list()) == 1
    metadata = store.metadata("latest")
    assert [entry.relative_path for entry in metadata.files] == ["one.txt", "two.txt"]


@pytest.mark.parametrize("preview", ["table", "diff"])
def test_apply_check_explicit_preview_reports_drift_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preview: str,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe, target = _out_recipe(tmp_path)

    result = run(
        ["apply", str(recipe), str(target), "--check", "--preview", preview],
    )

    assert result.exit_code == 3, result.output
    assert not (target / "out.txt").exists()
    assert BackupStore(library_dir() / "backups").list() == []
    assert "Recipe preview:" in result.stderr
    if preview == "table":
        assert str(target / "out.txt") in result.stderr
        assert "changes" in result.stderr
    else:
        assert "--- /dev/null\n+++ b/out.txt\n" in result.stderr


def test_confirm_accept_applies_changes(tmp_path: Path, terminal: ScriptedTerminal) -> None:
    recipe, target = _out_recipe(tmp_path)
    terminal.answers(True)

    result = run(["apply", str(recipe), str(target), "--preview", "none"])

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text(encoding="utf-8") == "hello\n"
    assert "cancelled; no changes made" not in result.stderr


def test_apply_stdin_confirms_on_the_controlling_terminal(
    tmp_path: Path, terminal: ScriptedTerminal
) -> None:
    recipe, target = _out_recipe(tmp_path)
    terminal.answers(True)

    result = run(["apply", str(recipe), "--stdin", "--preview", "none"], input=f"{target}\n")

    assert result.exit_code == 0, result.output
    assert terminal.asked == [("confirm", "Apply to 1 target?")]
    assert (target / "out.txt").read_text(encoding="utf-8") == "hello\n"


def test_apply_check_reports_drift_without_writing_or_backing_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe, target = _out_recipe(tmp_path)

    drift = run(
        ["apply", str(recipe), str(target), "--check", "--format", "json"],
    )

    assert drift.exit_code == 3, drift.output
    assert not (target / "out.txt").exists()
    assert BackupStore(library_dir() / "backups").list() == []
    rows = json.loads(drift.stdout)
    assert rows[0]["action"] == "planned"
    assert rows[0]["files_changed"] == 1
    assert "Recipe preview:" in drift.stderr
    assert str(target / "out.txt") not in drift.stderr
    assert "action" not in drift.stderr
    assert "changes" not in drift.stderr

    (target / "out.txt").write_text("hello\n")
    clean = run(
        ["apply", str(recipe), str(target), "--check", "--format", "json"],
    )

    assert clean.exit_code == 0, clean.output
    rows = json.loads(clean.stdout)
    assert rows[0]["action"] == "unchanged"
    assert rows[0]["files_changed"] == 0
    assert "Recipe preview:" in clean.stderr
    assert str(target / "out.txt") not in clean.stderr


def test_apply_stdin_requires_yes_and_resolves_workspace_status_pipe(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    repo.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo_status",
            "record": {"path": str(workspace), "target_path": str(repo), "repo": "api"},
        }
    )

    refused = run(["apply", str(recipe), "--stdin"], input=payload + "\n")
    assert refused.exit_code != 0
    assert "requires --yes" in refused.output

    result = run(
        ["apply", str(recipe), "--stdin", "--yes"],
        input=payload + "\n",
    )
    assert result.exit_code == 0, result.output
    assert (repo / "out.txt").read_text() == "hello\n"


def _envelope(kind: str | None, record: dict[str, object]) -> str:
    return json.dumps({"untaped": "1", "kind": kind, "record": record}) + "\n"


def test_apply_stdin_reads_a_kind_less_row_by_its_target_path(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    repo = tmp_path / "api"
    repo.mkdir()

    piped = _envelope(None, {"name": "api", "target_path": str(repo), "extra": [1]})
    result = run(["apply", str(recipe), "--stdin", "--yes"], input=piped)

    assert result.exit_code == 0, result.output
    assert (repo / "out.txt").read_text() == "hello\n"


def test_apply_stdin_refuses_a_kind_no_plugin_declares(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    repo = tmp_path / "api"
    repo.mkdir()

    piped = _envelope("acme.thing", {"path": str(repo)})
    result = run(["apply", str(recipe), "--stdin", "--yes"], input=piped)

    assert result.exit_code != 0
    assert "no installed plugin declares the kind 'acme.thing'" in result.said
    assert not (repo / "out.txt").exists()


def test_apply_stdin_refuses_a_row_its_kind_does_not_validate(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    repo = tmp_path / "api"
    repo.mkdir()

    piped = _envelope("workspace.repo_status", {"target_path": str(repo)})
    result = run(["apply", str(recipe), "--stdin", "--yes"], input=piped)

    assert result.exit_code == 1, result.output
    assert "line 1:" in result.said
    assert not (repo / "out.txt").exists()


def test_apply_stdin_names_the_row_that_names_no_target(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    repo = tmp_path / "api"
    repo.mkdir()

    piped = _envelope(None, {"path": str(repo)}) + _envelope(None, {"name": "api"})
    result = run(["apply", str(recipe), "--stdin", "--yes"], input=piped)

    assert result.exit_code != 0
    assert "line 2: a piped row names no target" in result.said
    assert not (repo / "out.txt").exists()


def test_apply_stdin_empty_input_still_errors(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)

    result = run(
        ["apply", str(recipe), "--stdin", "--yes"],
        input="",
    )

    assert result.exit_code != 0
    assert "error: nothing received on stdin" in result.output


def test_apply_check_allows_stdin_without_yes(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path)

    result = run(
        ["apply", str(recipe), "--stdin", "--check"],
        input=str(target) + "\n",
    )

    assert result.exit_code == 3, result.output
    assert "requires --yes" not in result.output
    assert not (target / "out.txt").exists()


def test_apply_stdin_without_yes_refuses_before_hooks_run(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    marker = tmp_path / "hook-ran"
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: validate\n"
        "    hook: touch\n"
        "    args:\n"
        f"      marker: {marker}\n"
    )
    write_hook_project(
        recipe_dir,
        public_name="touch",
        module_name="touch",
        code=(
            "from pathlib import Path\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    Path(args['marker']).write_text('ran')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    refused = run(
        ["apply", str(recipe_dir), "--stdin"],
        input=str(target) + "\n",
    )

    assert refused.exit_code == 2
    assert "error: untaped recipe apply requires --yes when not interactive" in refused.output
    assert not marker.exists()


@pytest.mark.parametrize(
    ("recipe_content", "expected"),
    [
        ("version: [\n", "invalid recipe YAML"),
        ("version: 2\nsteps: []\n", "invalid recipe"),
        ("version: 1\nname: demo\nsteps: []\n", "name is not allowed here"),
    ],
)
def test_apply_recipe_load_errors_are_reported_cleanly(
    tmp_path: Path,
    recipe_content: str,
    expected: str,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(recipe_content)
    target = tmp_path / "target"
    target.mkdir()

    result = run(["apply", str(recipe), str(target), "--yes"])

    assert result.exit_code != 0
    assert "error: " in result.output
    assert expected in result.output
    # Domain errors name the file and never leak pydantic internals.
    assert str(recipe) in result.output
    assert "extra_forbidden" not in result.output
    assert "Traceback" not in result.output


def test_apply_missing_recipe_is_reported_cleanly(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()

    result = run(["apply", "missing", str(target), "--yes"])

    assert result.exit_code != 0
    assert "error: recipe not found: 'missing'" in result.output
    assert "Traceback" not in result.output


def test_apply_var_values_keep_equals_and_unknown_vars_are_rejected(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("service={{ service }}\n")
    target = tmp_path / "target"
    target.mkdir()

    result = run(
        ["apply", str(recipe), str(target), "--var", "service=api=v1", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "service=api=v1\n"

    rejected = run(
        ["apply", str(recipe), str(target), "--var", "servcie=typo", "--yes"],
    )
    assert rejected.exit_code != 0
    assert "unknown input" in rejected.output


@pytest.mark.parametrize("value", ["[a, b]", "a: b", "{x: y}", "true"])
def test_apply_scalar_var_values_that_look_like_yaml_stay_literal_strings(
    tmp_path: Path,
    value: str,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("service={{ service }}\n")
    target = tmp_path / value.replace("/", "_")
    target.mkdir()

    result = run(
        ["apply", str(recipe), str(target), "--var", f"service={value}", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == f"service={value}\n"


_COLS = "cols: {type: list, items: str, required: true}"


@pytest.mark.parametrize(
    ("declared", "args", "expected"),
    [
        pytest.param(
            "replicas: {type: int, required: true}",
            ["--var", "replicas=x"],
            "input 'replicas': cannot coerce value to int",
            id="scalar-coercion-error-names-input",
        ),
        pytest.param(
            "replicas: {type: int, required: true}",
            ["--var", "replicas=[a, b]"],
            "cannot coerce value to int",
            id="scalar-error-ignores-yaml-look",
        ),
        pytest.param(_COLS, ["--var", "cols=[a, b]"], {"cols": ["a", "b"]}, id="yaml-list"),
        pytest.param(
            _COLS, ["--var", "cols=["], "input 'cols' expects YAML list:", id="malformed-yaml"
        ),
        pytest.param(
            _COLS,
            ["--var", "cols=enabled"],
            "input 'cols' expects YAML list: parsed value is not a list",
            id="yaml-scalar-for-list",
        ),
        pytest.param(
            _COLS, ["--vars-file", "{vars_file}"], {"cols": ["a", "b"]}, id="vars-file-native"
        ),
        pytest.param(
            "tokens: {type: list, sensitive: true, required: true}",
            ["--var", "tokens=[alpha, beta]"],
            {"tokens": "***"},
            id="sensitive-structured-redacted-whole",
        ),
    ],
)
def test_apply_var_values_parse_by_declared_type(
    tmp_path: Path,
    declared: str,
    args: list[str],
    expected: str | dict[str, object],
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(f"version: 1\ninputs:\n  {declared}\nsteps: []\n")
    vars_file = tmp_path / "vars.yml"
    vars_file.write_text("cols:\n  - a\n  - b\n")
    target = tmp_path / "target"
    target.mkdir()

    result = run(
        [
            *("apply", str(recipe), str(target), "--yes", "--format", "json"),
            *(arg.format(vars_file=vars_file) for arg in args),
        ],
    )

    if isinstance(expected, dict):
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)[0]["inputs"] == expected
        assert "alpha" not in result.stdout
    else:
        assert result.exit_code != 0
        assert expected in result.stderr
        assert "Traceback" not in result.output
        if "int" in declared:
            assert "expects YAML" not in result.output


@pytest.mark.parametrize(
    ("vars_file", "extra", "code", "message"),
    [
        (None, [], 2, "--vars-file: Path does not point to a file"),
        ("[unclosed\n", [], 1, "--vars-file file {path} is invalid YAML"),
        ("- a\n", [], 1, "--vars-file file {path} must contain a mapping"),
        (
            "{}\n",
            ["--hook-timeout", "-1"],
            2,
            "--hook-timeout: Input should be greater than or equal to 0",
        ),
    ],
)
def test_apply_rejects_bad_vars_file_and_hook_timeout(
    tmp_path: Path, vars_file: str | None, extra: list[str], code: int, message: str
) -> None:
    recipe, target = _out_recipe(tmp_path)
    path = tmp_path / "vars.yml"
    if vars_file is not None:
        path.write_text(vars_file)

    result = run(["apply", str(recipe), str(target), "--vars-file", str(path), *extra, "--dry-run"])

    assert result.exit_code == code, result.output
    assert message.format(path=path) in result.stderr
    assert "Traceback" not in result.output


def test_apply_vars_files_merge_in_order_and_var_wins(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  a: {type: str}\n  b: {type: str}\n  c: {type: str}\nsteps: []\n"
    )
    first = tmp_path / "first.yml"
    first.write_text("a: first\nb: first\n")
    second = tmp_path / "second.yml"
    second.write_text("b: second\nc: second\n")
    target = tmp_path / "target"
    target.mkdir()

    result = run(
        [
            *("apply", str(recipe), str(target), "--dry-run", "--format", "json"),
            *("--var", "c=cli", "--vars-file", str(first), "--vars-file", str(second)),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["inputs"] == {"a": "first", "b": "second", "c": "cli"}


_PROMPTED_RECIPE = (
    "version: 1\n"
    "inputs:\n"
    "  owner: {type: str, scope: global, required: true, description: Owning team.}\n"
    "  token: {type: str, scope: global, required: true, sensitive: true}\n"
    "  service: {type: str, scope: target, required: true}\n"
    "  region: {type: str, default: eu}\n"
    "  note: {type: str}\n"
    "steps: []\n"
)


def test_apply_prompts_only_for_missing_required_inputs_on_a_terminal(
    tmp_path: Path, terminal: ScriptedTerminal
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(_PROMPTED_RECIPE)
    target = tmp_path / "api"
    target.mkdir()
    terminal.types("platform", "s3cret", "api")

    result = run(["apply", str(recipe), str(target), "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert terminal.asked == [
        ("text", "owner (Owning team.)"),
        ("secret", "token"),
        ("text", f"service for {target}"),
    ]
    assert json.loads(result.stdout)[0]["inputs"] == {
        "owner": "platform",
        "token": "***",
        "service": "api",
        "region": "eu",
    }


@pytest.mark.parametrize(
    ("interactive", "extra"),
    [
        pytest.param(False, [], id="no-terminal"),
        pytest.param(True, ["--non-interactive"], id="non-interactive"),
        pytest.param(True, ["--check"], id="check-asks-nothing"),
    ],
)
def test_apply_never_prompts_without_a_terminal_or_with_non_interactive(
    tmp_path: Path, terminal: ScriptedTerminal, interactive: bool, extra: list[str]
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(_PROMPTED_RECIPE)
    target = tmp_path / "api"
    target.mkdir()
    if interactive:
        terminal.answers()

    missing_global = run(["apply", str(recipe), str(target), "--dry-run", *extra])
    missing_target = run(
        [
            *("apply", str(recipe), str(target), "--dry-run", "--format", "json", *extra),
            *("--var", "owner=platform", "--var", "token=s3cret"),
        ]
    )

    assert terminal.asked == []
    assert missing_global.exit_code == 1, missing_global.output
    assert (
        "error: missing required input: owner; pass --var owner=VALUE or --vars-file FILE"
        in missing_global.stderr
    )
    assert missing_target.exit_code == 1, missing_target.output
    row = json.loads(missing_target.stdout)[0]
    assert row["action"] == "failed"
    assert row["detail"].startswith("missing required input: service;")


def test_apply_ctrl_c_at_a_prompt_exits_130_without_further_prompts(
    tmp_path: Path, terminal: ScriptedTerminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  service: {type: str, scope: target, required: true}\nsteps: []\n"
    )
    targets = [tmp_path / name for name in ("api", "web", "worker")]
    for target in targets:
        target.mkdir()
    asked: list[str] = []

    def interrupt(message: str, *, default: str | None) -> str:
        asked.append(message)
        raise KeyboardInterrupt

    monkeypatch.setattr(terminal.answers(), "text", interrupt)

    result = run(["apply", str(recipe), *map(str, targets), "--dry-run", "--format", "json"])

    assert result.exit_code == 130, result.output
    assert asked == [f"service for {targets[0]}"]
    assert result.stdout == ""


def test_apply_derives_target_inputs_and_redacts_outcome_rows(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service:\n"
        "    type: str\n"
        "    required: true\n"
        "    from: '{{ target.name }}'\n"
        "  token:\n"
        "    type: str\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("{{ service }} {{ token }}\n")
    target = tmp_path / "api"
    target.mkdir()

    result = run(
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "token=secret",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "api secret\n"
    rows = json.loads(result.stdout)
    assert rows[0]["inputs"] == {"service": "api", "token": "***"}
    assert "secret" not in result.stdout


def test_apply_sensitive_target_input_coercion_error_does_not_leak_secret(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: int\n"
        "    sensitive: true\n"
        "    required: true\n"
        "    from: '{{ record.token }}'\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo_status",
            "record": {"path": str(target), "token": secret},
        }
    )

    result = run(
        ["apply", str(recipe), "--stdin", "--dry-run", "--format", "json"],
        input=payload + "\n",
    )

    assert result.exit_code == 1, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "failed"
    assert rows[0]["detail"] == "input 'token': cannot coerce value to int"
    assert rows[0]["inputs"] == {}


def test_apply_sensitive_global_input_coercion_error_does_not_leak_secret(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: int\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    result = run(
        ["apply", str(recipe), str(target), "--var", f"token={secret}", "--dry-run"],
    )

    assert result.exit_code != 0
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "cannot coerce value to int" in result.output


def test_apply_sensitive_inputs_redact_warnings_and_suppress_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    secret = 'TOP-SECRET-9000"\\tail'
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: str\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps:\n"
        "  - type: validate\n"
        "    hook: leak\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (recipe_dir / "template.txt").write_text("token={{ token }}\n")
    write_hook_project(
        recipe_dir,
        public_name="leak",
        module_name="leak",
        code=(
            "import json\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    helpers.warn(json.dumps({'warning': inputs['token']}))\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "api"
    target.mkdir()

    result = run(
        [
            "apply",
            str(recipe_dir),
            str(target),
            "--var",
            f"token={secret}",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "diff suppressed for target with sensitive inputs" not in result.stderr
    assert str(target) in result.stderr
    assert "files_changed" in result.stderr
    assert "out.txt" not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["warnings"] == ["diagnostic suppressed for target with sensitive inputs"]
    assert rows[0]["inputs"] == {"token": "***"}


def _write_skip_recipe_project(tmp_path: Path) -> Path:
    recipe_dir = tmp_path / "scoped"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\nsteps:\n"
        "  - type: validate\n    hook: scope\n"
        "  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (recipe_dir / "template.txt").write_text("applied\n")
    write_hook_project(
        recipe_dir,
        public_name="scope",
        module_name="scope",
        code=(
            "def validate(*, inputs, target, args, helpers):\n"
            "    if (target / 'wanted.txt').exists():\n"
            "        return helpers.pass_()\n"
            "    return helpers.skip('out of scope')\n"
        ),
    )
    return recipe_dir


def test_apply_skip_verdict_end_to_end(tmp_path: Path) -> None:
    recipe_dir = _write_skip_recipe_project(tmp_path)
    in_target = tmp_path / "app-in"
    in_target.mkdir()
    (in_target / "wanted.txt").write_text("x")
    out_target = tmp_path / "app-out"
    out_target.mkdir()
    backups = BackupStore(library_dir() / "backups")

    # Boundary population: every target skips -> exit 0, no writes, no backup.
    all_skip = run(
        ["apply", str(recipe_dir), str(out_target), "--yes", "--format", "json"],
    )
    assert all_skip.exit_code == 0, all_skip.output
    assert json.loads(all_skip.stdout)[0]["action"] == "skipped"
    assert not (out_target / "out.txt").exists()
    assert backups.list() == []
    assert "1 skipped" in all_skip.stderr

    # --check: a skip is success, never counted as drift.
    checked = run(
        ["apply", str(recipe_dir), str(out_target), "--check", "--format", "json"],
    )
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.stdout)[0]["action"] == "skipped"

    # Mixed run: applicable target applies (and is backed up), other skips.
    mixed = run(
        [
            "apply",
            str(recipe_dir),
            str(in_target),
            str(out_target),
            "--yes",
            "--format",
            "json",
        ],
    )
    assert mixed.exit_code == 0, mixed.output
    rows = {row["target_path"]: row for row in json.loads(mixed.stdout)}
    assert rows[str(in_target)]["action"] == "applied"
    assert rows[str(out_target)]["action"] == "skipped"
    assert (in_target / "out.txt").read_text() == "applied\n"
    assert not (out_target / "out.txt").exists()
    assert len(backups.list()) == 1
    assert "1 applied" in mixed.stderr
    assert "1 skipped" in mixed.stderr

    # Pipe record carries the new skipped status.
    piped = run(
        ["apply", str(recipe_dir), str(out_target), "--dry-run", "--format", "pipe"],
    )
    assert piped.exit_code == 0, piped.output
    record = json.loads(piped.stdout)
    assert record["kind"] == "recipe.apply_outcome"
    assert record["record"]["action"] == "skipped"

    # Skip flows through --stdin targets too.
    from_stdin = run(
        ["apply", str(recipe_dir), "--stdin", "--yes", "--format", "json"],
        input=f"{out_target}\n",
    )
    assert from_stdin.exit_code == 0, from_stdin.output
    assert json.loads(from_stdin.stdout)[0]["action"] == "skipped"


def test_apply_transform_warn_reaches_outcome(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "noted"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\nsteps:\n  - type: transform\n    file: data.txt\n    hook: note\n"
    )
    write_hook_project(
        recipe_dir,
        public_name="note",
        module_name="note",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    helpers.warn('noticed something')\n"
            "    helpers.warn('and another')\n"
            "    return content + 'noted\\n'\n"
        ),
    )
    target = tmp_path / "app"
    target.mkdir()
    (target / "data.txt").write_text("start\n")

    result = run(
        ["apply", str(recipe_dir), str(target), "--dry-run", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    warnings = json.loads(result.stdout)[0]["warnings"]
    assert "noticed something" in warnings
    assert "and another" in warnings


def test_apply_sensitive_inputs_redact_hook_failures(
    tmp_path: Path,
) -> None:
    secret = 'TOP-SECRET-9000"\\tail'
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: str\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: config.txt\n"
        "    hook: leak\n"
    )
    write_hook_project(
        recipe_dir,
        public_name="leak",
        module_name="leak",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    raise RuntimeError(f\"failed {inputs['token']!r}\")\n"
        ),
    )
    target = tmp_path / "api"
    target.mkdir()
    (target / "config.txt").write_text("before\n")

    result = run(
        [
            "apply",
            str(recipe_dir),
            str(target),
            "--var",
            f"token={secret}",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "failed"
    assert rows[0]["detail"] == (
        "target planning failed; diagnostic suppressed for target with sensitive inputs"
    )
    # The structured error says the same, and a raising hook is a failed run.
    assert rows[0]["error"]["message"] == rows[0]["detail"]
    assert (rows[0]["error"]["category"], rows[0]["error"]["system"]) == ("failed", "local")
    assert rows[0]["inputs"] == {"token": "***"}


@pytest.mark.parametrize(
    ("declared", "args", "expected"),
    [
        pytest.param(
            "replicas: {type: int, scope: target}",
            ["--var", "replicas=not-an-int"],
            "cannot coerce value to int",
            id="invalid-fixed-target-input",
        ),
        pytest.param(
            "service: {type: str, from: '{{ target.name'}",
            [],
            "invalid input source expression for service",
            id="invalid-jinja-source",
        ),
        pytest.param(
            "service: {type: str, from: '{% for item in [target.name] %}{{ item }}{% endfor %}'}",
            [],
            "invalid input source expression for service",
            id="jinja-control-block",
        ),
    ],
)
def test_apply_invalid_inputs_fail_before_target_rows(
    tmp_path: Path, declared: str, args: list[str], expected: str
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(f"version: 1\ninputs:\n  {declared}\nsteps: []\n")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    result = run(
        ["apply", str(recipe), str(first), str(second), *args, "--dry-run", "--format", "json"],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert expected in result.stderr


def test_apply_outcome_includes_plan_warnings_in_every_format(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: missing.yml\n"
        "    optional: true\n"
        "    hook: unused\n"
        "  - type: remove\n"
        "    globs:\n"
        "      - '**/*.generated'\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    warnings = [
        "optional transform skipped missing file: missing.yml",
        "globs matched no files: **/*.generated",
    ]

    def printed(fmt: str) -> str:
        result = run(["apply", str(recipe), str(target), "--dry-run", "--format", fmt])
        assert result.exit_code == 0, result.output
        return result.stdout

    assert json.loads(printed("json"))[0]["warnings"] == warnings
    assert "- 'optional transform skipped missing file: missing.yml'" in printed("yaml")
    pipe_row = json.loads(printed("pipe"))
    assert pipe_row["kind"] == "recipe.apply_outcome"
    assert pipe_row["record"]["warnings"] == warnings


def test_apply_table_inputs_cell_renders_key_value_pairs_not_dict_repr(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  service:\n    type: str\n  cols:\n    type: list\nsteps: []\n"
    )
    target = tmp_path / "target"
    target.mkdir()

    result = run(
        [
            *("apply", str(recipe), str(target), "--yes", "--columns", "inputs"),
            *("--var", "service=api", "--var", "cols=[a, b]"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "service=api, cols=a, b" in result.stdout
    assert "{'service'" not in result.stdout


def test_apply_bulk_invocation_backs_up_only_successful_targets_in_one_bundle(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: config.txt\n"
    )
    (tmp_path / "template.txt").write_text("after\n")
    first = tmp_path / "first"
    second = tmp_path / "second"
    for target in (first, second):
        target.mkdir()
        (target / "config.txt").write_text("before\n")
    missing = tmp_path / "missing"

    result = run(
        [
            *("apply", str(recipe), str(first), str(second), str(missing)),
            *("--yes", "--format", "json"),
        ],
    )

    assert result.exit_code != 0
    assert [row["action"] for row in json.loads(result.stdout)] == ["applied", "applied", "failed"]
    assert (first / "config.txt").read_text() == "after\n"
    store = BackupStore(library_dir() / "backups")
    [bundle] = store.list()
    assert len(store.metadata(bundle.id).files) == 2


def test_apply_record_valued_source_fails_without_copying_record_contents(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  debug: {type: str, from: '{{ record }}'}\nsteps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo_status",
            "record": {"path": str(target), "token": secret},
        }
    )

    result = run(
        ["apply", str(recipe), "--stdin", "--dry-run", "--format", "json"],
        input=payload + "\n",
    )

    assert result.exit_code == 1, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "failed"
    assert rows[0]["detail"] == "derived input value must be a scalar"


def test_apply_outcome_inputs_render_in_yaml_and_table(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true, from: '{{ target.name }}'}\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    yaml_result = run(
        ["apply", str(recipe), str(target), "--dry-run", "--format", "yaml"],
    )
    assert yaml_result.exit_code == 0, yaml_result.output
    assert "inputs:" in yaml_result.stdout
    assert "service: api" in yaml_result.stdout

    table_result = run(["apply", str(recipe), str(target), "--dry-run"])
    assert table_result.exit_code == 0, table_result.output
    assert "service" not in table_result.stdout
    added = run(["apply", str(recipe), str(target), "--dry-run", "--columns", "+inputs"])
    assert added.exit_code == 0, added.output
    assert "inputs" in added.stdout
    assert "service=api" in added.stdout


def test_apply_derives_inputs_from_pipe_record_and_input_from_override(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service:\n"
        "    type: str\n"
        "    required: true\n"
        "    from:\n"
        "      - '{{ record.repo }}'\n"
        "      - '{{ target.name }}'\n"
        "  owner:\n"
        "    type: str\n"
        "    from: '{{ record.team }}'\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("{{ service }} {{ owner }}\n")
    workspace = tmp_path / "workspace"
    target = workspace / "api"
    target.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo_status",
            "record": {
                "path": str(workspace),
                "target_path": str(target),
                "repo": "api",
                "team": "platform",
            },
        }
    )

    result = run(
        [
            "apply",
            str(recipe),
            "--stdin",
            "--yes",
            "--input-from",
            "owner={{ target.parent_name }}",
            "--format",
            "pipe",
        ],
        input=payload + "\n",
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "api workspace\n"
    row = json.loads(result.stdout)
    assert row["kind"] == "recipe.apply_outcome"
    assert row["record"]["inputs"] == {"service": "api", "owner": "workspace"}


def test_apply_derives_structured_input_from_pipe_record(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  collections:\n"
        "    type: list\n"
        "    items: str\n"
        "    from: '{{ record.collections }}'\n"
        "steps: []\n"
    )
    workspace = tmp_path / "workspace"
    target = workspace / "api"
    target.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo_status",
            "record": {
                "path": str(workspace),
                "target_path": str(target),
                "collections": ["ansible.builtin", "community.general"],
            },
        }
    )

    result = run(
        ["apply", str(recipe), "--stdin", "--yes", "--format", "pipe"],
        input=payload + "\n",
    )

    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert row["kind"] == "recipe.apply_outcome"
    assert row["record"]["inputs"] == {"collections": ["ansible.builtin", "community.general"]}
    assert next(iter(row["record"])) == "target_path"


def test_apply_rejects_input_from_conflicts_and_global_scope(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true, from: '{{ target.name }}'}\n"
        "  owner: {type: str, scope: global, required: true}\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    conflict = run(
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "service=fixed",
            "--input-from",
            "service={{ target.name }}",
            "--var",
            "owner=platform",
            "--dry-run",
        ],
    )
    assert conflict.exit_code == 2  # a usage error
    assert "cannot combine --var/--vars-file and --input-from for service" in conflict.output

    global_source = run(
        [
            "apply",
            str(recipe),
            str(target),
            "--input-from",
            "owner={{ target.name }}",
            "--dry-run",
        ],
    )
    assert global_source.exit_code != 0
    assert "scope global" in global_source.output


def test_apply_stdin_targets_never_prompt_for_missing_inputs(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  service: {type: str, scope: target, required: true}\nsteps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    result = run(
        ["apply", str(recipe), "--stdin", "--dry-run", "--format", "json"],
        input=str(target) + "\n",
    )

    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)[0]["detail"].startswith("missing required input: service;")


def test_apply_backup_metadata_records_redacted_per_target_inputs(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true, from: '{{ target.name }}'}\n"
        "  token: {type: str, scope: global, sensitive: true, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("{{ service }} {{ token }}\n")
    target = tmp_path / "api"
    target.mkdir()
    (target / "out.txt").write_text("before\n")

    result = run(
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "token=secret",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    metadata = BackupStore(library_dir() / "backups").metadata("latest")
    assert metadata.files[0].inputs == {"service": "api", "token": "***"}
    assert "secret" not in metadata.model_dump_json()


def test_ansible_style_optional_multi_file_recipe_acceptance(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    files:\n"
        "      - local.yml\n"
        "      - site.yml\n"
        "      - playbooks/deploy.yml\n"
        "    optional: true\n"
        "    hook: add_play_collections\n"
        "  - type: remove\n"
        "    files:\n"
        "      - ansible.cfg\n"
    )
    write_hook_project(
        recipe_dir,
        public_name="add_play_collections",
        module_name="add_play_collections",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + '# collections added to ' + file.name + '\\n'\n"
        ),
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "playbooks").mkdir()
    (first / "local.yml").write_text("- hosts: localhost\n")
    (first / "ansible.cfg").write_text("[defaults]\n")
    (second / "local.yml").write_text("- hosts: localhost\n")
    (second / "site.yml").write_text("- hosts: all\n")
    (second / "playbooks" / "deploy.yml").write_text("- hosts: deploy\n")
    (second / "ansible.cfg").write_text("[defaults]\n")

    result = run(
        ["apply", str(recipe_dir), str(first), str(second), "--yes", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["warnings"] == [
        "optional transform skipped missing file: site.yml",
        "optional transform skipped missing file: playbooks/deploy.yml",
    ]
    assert rows[1]["warnings"] == []
    assert "# collections added to local.yml" in (first / "local.yml").read_text()
    assert not (first / "ansible.cfg").exists()
    assert "# collections added to local.yml" in (second / "local.yml").read_text()
    assert "# collections added to site.yml" in (second / "site.yml").read_text()
    assert "# collections added to deploy.yml" in (second / "playbooks" / "deploy.yml").read_text()
    assert not (second / "ansible.cfg").exists()


def test_explicit_single_file_recipe_does_not_use_sibling_hook_project(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: transform\n    file: local.yml\n    hook: sibling\n"
    )
    write_hook_project(
        tmp_path,
        public_name="sibling",
        module_name="sibling",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + 'changed\\n'\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")

    result = run(
        ["apply", str(recipe), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0
    assert "hook not found: 'sibling'" in result.output
    assert (target / "local.yml").read_text() == "---\n"


def test_external_hook_args_with_yaml_dates_are_rejected_before_worker(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: local.yml\n"
        "    hook: stamp\n"
        "    args:\n"
        "      day: 2026-06-19\n"
    )
    write_hook_project(
        recipe_dir,
        public_name="stamp",
        module_name="stamp",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + 'day=' + args['day'] + '\\n'\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")

    result = run(
        ["apply", str(recipe_dir), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0
    assert "is not JSON-serializable" in result.output
    assert (target / "local.yml").read_text() == "---\n"


def test_apply_help_says_dry_run_and_check_still_run_hooks() -> None:
    result = run(["apply", "--help"])

    help_text = " ".join(result.stdout.split())
    assert "--dry-run" in help_text
    assert "Pack hooks still run to compute the plan." in help_text
    assert "pack hooks still run." in help_text


def test_apply_table_leaves_out_the_recipe_and_inputs(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path)

    result = run(["apply", str(recipe), str(target), "--dry-run", "--columns", "?"])

    assert result.exit_code == 0, result.output
    assert default_columns(result.stderr) == {
        "target_path",
        "action",
        "files_changed",
        "warnings",
        "detail",
    }


def test_apply_unchanged_targets_report_unchanged_status(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    changing = tmp_path / "changing"
    changing.mkdir()
    unchanged = tmp_path / "unchanged"
    unchanged.mkdir()
    (unchanged / "out.txt").write_text("hello\n")

    result = run(
        ["apply", str(recipe), str(changing), str(unchanged), "--yes", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = {row["target_path"]: row["action"] for row in json.loads(result.stdout)}
    assert rows[str(changing)] == "applied"
    assert rows[str(unchanged)] == "unchanged"
    assert "planned" not in result.stdout
    assert "1 applied, 1 unchanged" in result.stderr


def test_apply_bare_ref_uses_library_even_when_matching_local_directory_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ansible" / "playbook").mkdir(parents=True)
    source = tmp_path / "source"
    write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    install_pack(source)
    target = tmp_path / "target"
    target.mkdir()

    result = run(["apply", "ansible/playbook", str(target), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "recipe not found" not in result.output


def test_apply_explicit_and_yaml_paths_load_from_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    recipe_dir = tmp_path / "a" / "b"
    recipe_dir.mkdir(parents=True)
    recipe = recipe_dir / "recipe.yml"
    recipe.write_text("version: 1\nsteps: []\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    explicit_dir = run(["apply", "./a/b/recipe.yml", str(target), "--dry-run"])
    yaml_suffix = run(["apply", "a/b/recipe.yml", str(target), "--dry-run"])

    assert explicit_dir.exit_code == 0, explicit_dir.output
    assert yaml_suffix.exit_code == 0, yaml_suffix.output


_BINARY = b"\x89PNG\r\n\x1a\n\x00\xff\xfe binary \x80\r\n"


def test_apply_copies_and_removes_binary_files_byte_exact(tmp_path: Path) -> None:
    pack = tmp_path / "mypack"
    write_pack(
        pack,
        manifest_name="mypack",
        recipes={"fix": "recipes/fix.yml"},
        recipe_body=(
            "version: 1\nsteps:\n"
            "  - type: copy\n    source: logo.png\n    dest: assets/logo.png\n"
            "  - type: remove\n    file: old.png\n"
        ),
    )
    (pack / "recipes" / "logo.png").write_bytes(_BINARY)
    target = tmp_path / "target"
    target.mkdir()
    (target / "old.png").write_bytes(_BINARY[::-1])

    diff = run(["apply", str(pack), str(target), "--preview", "diff", "--dry-run"])
    applied = run(["apply", str(pack), str(target), "--yes"])

    assert diff.exit_code == 0, diff.output
    assert "Binary file assets/logo.png differs" in diff.stderr
    assert applied.exit_code == 0, applied.output
    assert (target / "assets" / "logo.png").read_bytes() == _BINARY
    assert not (target / "old.png").exists()

    restored = run(["backups", "restore", "latest", "--yes"])

    assert restored.exit_code == 0, restored.output
    assert (target / "old.png").read_bytes() == _BINARY[::-1]
    assert not (target / "assets" / "logo.png").exists()


@pytest.mark.parametrize("installed", [True, False])
def test_apply_pack_with_several_recipes_asks_for_one(tmp_path: Path, installed: bool) -> None:
    source = tmp_path / "acme"
    write_pack(
        source,
        manifest_name="acme",
        recipes={"one": "recipes/one.yml", "two": "recipes/two.yml"},
    )
    if installed:
        install_pack(source)
    target = tmp_path / "target"
    target.mkdir()

    ref = "acme" if installed else str(source)
    result = run(["apply", ref, str(target), "--yes"])

    assert result.exit_code != 0
    assert "pack 'acme' has 2 recipes" in result.stderr
    assert "acme/one" in result.stderr
    assert "acme/two" in result.stderr


@pytest.mark.parametrize(
    ("ref", "installed", "expected", "hinted"),
    [
        ("./demo", True, "recipe file not found", True),
        ("./demo.yml", True, "recipe file not found: demo.yml", True),
        ("./demo", False, "recipe file not found", False),
        ("./my recipe.yml", True, "recipe file not found: my recipe.yml", False),
        ("./nope.yml", False, "recipe file not found: nope.yml", False),
    ],
)
def test_explicit_path_miss_hints_at_matching_library_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ref: str,
    installed: bool,
    expected: str,
    hinted: bool,
) -> None:
    if installed:
        source = tmp_path / "source"
        write_pack(source, manifest_name="pack", recipes={"demo": "recipes/demo.yml"})
        install_pack(source)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "demo").mkdir()

    applied = run(["apply", ref, str(tmp_path), "--yes"])

    assert applied.exit_code == 1
    assert expected in applied.stderr
    assert ("did you mean the library ref 'demo'?" in applied.stderr) is hinted
    assert "safe library name" not in applied.stderr
    if ref.endswith(".yml"):
        checked = run(["validate", ref, "--format", "json"])
        assert checked.exit_code == 1
        assert expected in checked.stderr
        assert "Traceback" not in applied.output + checked.output


@pytest.mark.parametrize(
    ("ref", "installed"),
    [(".", False), ("./", False), ("{pack}", False), ("acme", True)],
)
def test_apply_pack_ref_picks_its_only_recipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ref: str,
    installed: bool,
) -> None:
    pack = tmp_path / "acme"
    write_pack(pack, manifest_name="acme", recipes={"fix": "recipes/fix.yml"})
    if installed:
        install_pack(pack)
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.chdir(pack)
    args = [ref.format(pack=pack), str(target), "--yes", "--format", "json"]
    if ref.startswith("."):
        args += ["--recipe", "fix"]

    result = run(["apply", *args])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["recipe"] == "acme/fix"
