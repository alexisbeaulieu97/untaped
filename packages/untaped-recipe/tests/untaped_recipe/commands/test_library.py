"""The recipe verbs at the top: ``list``, ``get``, ``edit``, ``init`` and ``validate``."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from recipe_pack_support import install_pack, write_hook_project, write_pack, write_pack_project
from recipe_test_support import library_dir, run
from untaped_recipe.domain.paths import is_path_ref


def _write_hookless_pack_project(root: Path, *, lock: bool = True) -> None:
    recipe = root / "recipes" / "seed" / "recipe.yml"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("version: 1\nsteps: []\n")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-hygiene"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.recipes]\n"
        '"seed" = { path = "recipes/seed/recipe.yml" }\n'
    )
    if lock:
        (root / "uv.lock").write_text("version = 1\n")


def test_add_and_check_hookless_pack_without_lock(tmp_path: Path) -> None:
    # Boundary population: a hookless pack with no uv.lock passes check and adds.
    pack = tmp_path / "pack"
    _write_hookless_pack_project(pack, lock=False)

    check = run(["validate", str(pack)])
    assert check.exit_code == 0, check.output

    added = run(["packs", "add", str(pack)])
    assert added.exit_code == 0, added.output
    assert (library_dir() / "packs" / "hygiene").exists()


def test_remove_that_deletes_part_of_a_pack_is_partial_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    assert run(["packs", "add", str(pack)]).exit_code == 0
    installed = library_dir() / "packs" / "demo"
    real_rmtree = shutil.rmtree

    def _half_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        if Path(path) != installed:
            real_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]
            return
        (installed / "pyproject.toml").unlink()
        raise OSError("permission denied")

    with monkeypatch.context() as patched:
        patched.setattr(shutil, "rmtree", _half_rmtree)
        result = run(["packs", "remove", "demo", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    assert row["action"] == "partial"
    assert "could not delete the rest: permission denied" in row["detail"]
    assert row["error"]["message"] == row["detail"]
    checked = run(["validate", "--format", "json"])
    assert [(row["name"], row["status"]) for row in json.loads(checked.stdout)] == [
        ("demo", "fail")
    ]
    retried = run(["packs", "remove", "demo", "--yes"])
    assert retried.exit_code == 0, retried.output
    assert not installed.exists()
    assert "demo" not in (library_dir() / "packs.toml").read_text()


@pytest.mark.parametrize(
    "args",
    [
        ["get", "missing"],
        ["backups", "get", "latest"],
    ],
)
def test_library_command_value_errors_are_reported_cleanly(args: list[str]) -> None:
    result = run(args)

    assert result.exit_code != 0
    assert "error: " in result.output
    assert "Traceback" not in result.output


def test_recipe_check_validates_package_assets_and_hooks(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("hello\n")
    (recipe_dir / "copy.txt").write_text("copy\n")
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
        "  - type: copy\n"
        "    source: copy.txt\n"
        "    dest: copy.txt\n"
        "  - type: validate\n"
        "    hook: check\n"
    )
    write_hook_project(
        recipe_dir,
        public_name="check",
        module_name="check",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )

    result = run(["validate", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "name": "recipe-hooks",
            "type": "pack",
            "status": "pass",
            "path": str(recipe_dir),
            "detail": None,
        }
    ]
    assert "Recipe preview:" not in result.stderr


def test_recipe_check_rejects_step_hook_kind_mismatch(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\nsteps:\n  - type: validate\n    hook: check\n"
    )
    package = recipe_dir / "src" / "recipe_hooks" / "hooks"
    package.mkdir(parents=True)
    (recipe_dir / "src" / "recipe_hooks" / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "check.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
    )
    (recipe_dir / "pyproject.toml").write_text(
        "[project]\n"
        'name = "recipe-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.recipes]\n"
        '"demo" = { path = "recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"check" = { module = "recipe_hooks.hooks.check" }\n'
    )
    (recipe_dir / "uv.lock").write_text("version = 1\n")

    result = run(["validate", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "fail"
    assert "validate step hook 'check' does not export a validate() function" in rows[0]["detail"]


@pytest.mark.parametrize(
    ("recipe_body", "expected"),
    [
        ("version: [\n", "invalid recipe YAML"),
        ("version: 2\nsteps: []\n", "invalid recipe"),
        (
            "version: 1\n"
            "steps:\n"
            "  - type: template\n"
            "    template: missing.txt\n"
            "    dest: out.txt\n",
            "template not found",
        ),
        (
            "version: 1\nsteps:\n  - type: validate\n    hook: missing\n",
            "hook not found",
        ),
        (
            "version: 1\n"
            "inputs:\n"
            "  service: {type: str, from: '{{ target.name | upper }}'}\n"
            "steps: []\n",
            "invalid input source expression for service",
        ),
        (
            "version: 1\n"
            "inputs:\n"
            "  kind: {type: str, default: web}\n"
            "steps:\n"
            "  - type: template\n"
            "    template: 'missing/{{ kind }}.txt'\n"
            "    dest: out.txt\n",
            "template not found",
        ),
        (
            "version: 1\ninputs:\n  replicas: {type: int, default: many}\nsteps: []\n",
            "default: cannot coerce value to int",
        ),
        (
            "version: 1\ninputs:\n  replicas: {type: int, required: true, default: 2}\nsteps: []\n",
            "required input cannot declare a default",
        ),
    ],
)
def test_recipe_check_reports_invalid_packages(
    tmp_path: Path,
    recipe_body: str,
    expected: str,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(recipe_body)

    result = run(
        ["validate", str(recipe_dir / "recipe.yml"), "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "fail"
    assert expected in rows[0]["detail"]
    assert "Traceback" not in result.output


@pytest.mark.parametrize("referenced", [True, False])
@pytest.mark.parametrize(
    ("breakage", "expected"),
    [
        ("lock", "missing uv.lock"),
        ("module", "hook module file not found"),
        ("runtime-dependency", "must not depend on untaped at runtime"),
    ],
)
def test_recipe_check_reports_broken_local_hook_projects(
    tmp_path: Path,
    referenced: bool,
    breakage: str,
    expected: str,
) -> None:
    # The local hook project is validated even when no step references it.
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    steps = "\n  - type: validate\n    hook: check\n" if referenced else " []\n"
    (recipe_dir / "recipe.yml").write_text(f"version: 1\nsteps:{steps}")
    write_hook_project(
        recipe_dir,
        public_name="check",
        module_name="check",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )
    pyproject = recipe_dir / "pyproject.toml"
    if breakage == "lock":
        (recipe_dir / "uv.lock").unlink()
    elif breakage == "module":
        (recipe_dir / "src" / "recipe_hooks" / "hooks" / "check.py").unlink()
    else:
        pyproject.write_text(
            pyproject.read_text().replace("dependencies = []", 'dependencies = ["untaped>=4,<5"]')
        )

    result = run(["validate", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "fail"
    assert expected in rows[0]["detail"]


def test_help_placeholders_and_ref_grammar_render_meaningfully(tmp_path: Path) -> None:

    init_help = run(["init", "--help"])
    hook_init_help = run(["hooks", "init", "--help"])
    hook_run_help = run(["hooks", "run", "--help"])
    apply_help = run(["apply", "--help"])

    assert "PACK/RECIPE reference" in init_help.stdout
    assert "PACK/HOOK reference" in hook_init_help.stdout
    assert "PACK/HOOK reference." in hook_run_help.stdout
    for result in (init_help, hook_init_help, hook_run_help):
        assert "REF  /." not in result.stdout
    assert "PACK/RECIPE" in apply_help.stdout
    assert "pack:recipe" not in apply_help.stdout


@pytest.mark.parametrize(
    ("pyproject", "expected"),
    [
        (
            "[project]\nname = 'recipe-hooks'\nversion = '0.1.0'\n\n"
            "[tool.untaped_recipe.recipes]\n"
            '"demo" = { path = "recipe.yml" }\n\n'
            "[tool.untaped_recipe.hooks]\n"
            '"bad-name" = { module = "recipe_hooks.hooks.check" }\n',
            "invalid hook name",
        ),
        (
            "[project]\nname = 'recipe-hooks'\nversion = '0.1.0'\n\n"
            "[tool.untaped_recipe.recipes]\n"
            '"demo" = { path = "recipe.yml" }\n\n'
            "[tool.untaped_recipe.hooks]\n"
            '"check" = { module =',
            "invalid pack project pyproject",
        ),
    ],
)
def test_recipe_check_validates_unreferenced_local_hook_project_metadata(
    tmp_path: Path,
    pyproject: str,
    expected: str,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text("version: 1\nsteps: []\n")
    (recipe_dir / "pyproject.toml").write_text(pyproject)
    (recipe_dir / "uv.lock").write_text("version = 1\n")

    result = run(["validate", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "fail"
    assert expected in rows[0]["detail"]


@pytest.mark.parametrize("command", [["packs", "init", "demo"], ["init", "demo/playbook"]])
def test_hook_only_init_flags_are_usage_errors_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    result = run([*command, "--kind", "validate"])

    assert result.exit_code == 2
    assert "--kind" in result.stderr
    assert not (tmp_path / "demo").exists()


def test_recipe_check_accepts_input_templated_asset_paths(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    (recipe_dir / "templates").mkdir(parents=True)
    (recipe_dir / "templates" / "web.txt").write_text("web\n")
    (recipe_dir / "files").mkdir()
    (recipe_dir / "files" / "web.cfg").write_text("cfg\n")
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "inputs:\n"
        "  kind: {type: str, default: web}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: 'templates/{{ kind }}.txt'\n"
        "    dest: out.txt\n"
        "  - type: copy\n"
        "    source: 'files/{{ kind }}.cfg'\n"
        "    dest: out.cfg\n"
    )

    result = run(["validate", str(recipe_dir / "recipe.yml"), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "pass"


def test_check_unknown_bare_ref_keeps_recipe_miss(tmp_path: Path) -> None:
    result = run(["validate", "not_a_builtin", "--format", "json"])

    assert result.exit_code == 1
    assert "recipe not found: 'not_a_builtin'" in result.stderr


@pytest.mark.parametrize("hook", ["yaml_edit", "shared"])
def test_recipe_get_hints_hooks_for_an_ambiguous_library_hook(tmp_path: Path, hook: str) -> None:
    for name in ("one", "two"):
        source = tmp_path / name
        write_pack(
            source,
            manifest_name=name,
            recipes={"playbook": "recipes/playbook.yml"},
            hooks={hook: f"{name}_pack.hooks.{hook}"},
        )
        install_pack(source)

    result = run(["get", hook])

    assert result.exit_code == 1, result.output
    assert f"recipe not found: '{hook}'" in result.stderr
    assert f"hint: run `untaped recipe hooks get {hook}`" in result.stderr


def test_unified_check_pack_validates_recipe_hook_exports(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
        recipe_body="version: 1\nsteps:\n  - type: validate\n    hook: check\n",
    )
    install_pack(source)

    result = run(["validate", "ansible", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "pass"


def test_check_flags_orphaned_tests_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    (source / "tests" / "playbook" / "basic" / "given").mkdir(parents=True)
    (source / "tests" / "renamed" / "old" / "given").mkdir(parents=True)
    install_pack(source)

    result = run(["validate", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    row = json.loads(result.stdout)[0]
    assert row["status"] == "fail"
    assert row["detail"] == "tests directory names no known recipe: renamed"


def test_check_reports_stale_lockfile_for_hook_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    install_pack(source)

    def _stale(project_root: Path) -> None:
        raise ValueError(f"lockfile is stale — run 'uv lock' in {project_root}")

    monkeypatch.setattr("untaped_recipe.infrastructure.pack_files.check_lock", _stale)
    result = run(["validate", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    row = json.loads(result.stdout)[0]
    assert row["status"] == "fail"
    assert "lockfile is stale — run 'uv lock' in" in row["detail"]


def test_check_hook_pack_without_lock_keeps_pack_error_exact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    install_pack(source)
    installed = library_dir() / "packs" / "ansible"
    (installed / "uv.lock").unlink()

    result = run(["validate", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)[0]["detail"] == f"pack project is missing uv.lock: {installed}"


def test_check_without_ref_reports_library_reconcile_and_pack_rows(tmp_path: Path) -> None:
    good_source = tmp_path / "good-source"
    stale_source = tmp_path / "stale-source"
    write_pack(good_source, manifest_name="good", recipes={"ok": "recipes/ok.yml"})
    write_pack(stale_source, manifest_name="stale", recipes={"old": "recipes/old.yml"})
    install_pack(good_source, name="good")
    install_pack(stale_source, name="stale")
    shutil.rmtree(library_dir() / "packs" / "stale")
    write_pack(
        library_dir() / "packs" / "orphan",
        manifest_name="orphan",
        recipes={"playbook": "recipes/playbook.yml"},
    )

    result = run(["validate", "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    errors = {row["detail"] for row in rows if row["status"] == "fail"}
    assert errors == {
        "pack 'stale' is in packs.toml but missing from packs/",
        "pack directory 'orphan' is not recorded in packs.toml",
    }
    passes = {row["name"] for row in rows if row["status"] == "pass"}
    assert passes == {"good", "orphan"}


def _install_good_and_broken_packs(tmp_path: Path) -> None:
    good = tmp_path / "good"
    write_pack(good, manifest_name="good", recipes={"playbook": "recipes/playbook.yml"})
    install_pack(good)
    broken = tmp_path / "broken"
    write_pack(broken, manifest_name="broken", recipes={"other": "recipes/other.yml"})
    install_pack(broken)
    installed = library_dir() / "packs" / "broken" / "pyproject.toml"
    installed.write_text("[project\nname = ", encoding="utf-8")


def test_list_skips_unparsable_pack_with_warning(tmp_path: Path) -> None:
    _install_good_and_broken_packs(tmp_path)

    result = run(["list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert [row["ref"] for row in json.loads(result.stdout)] == ["good/playbook"]
    assert "broken" in result.stderr
    assert "warning" in result.stderr.lower()


def test_check_reports_error_row_for_unparsable_pack(tmp_path: Path) -> None:
    _install_good_and_broken_packs(tmp_path)

    result = run(["validate", "--format", "json"])

    rows = {row["name"]: row for row in json.loads(result.stdout)}
    assert rows["good"]["status"] == "pass"
    assert rows["broken"]["status"] == "fail"
    assert "pyproject" in rows["broken"]["detail"]
    # the TOML parse detail is included, not just the file path
    assert "line" in rows["broken"]["detail"]
    assert result.exit_code != 0


def test_resolution_ignores_unparsable_pack_unless_named(tmp_path: Path) -> None:
    _install_good_and_broken_packs(tmp_path)

    shown = run(["get", "playbook", "--format", "json"])
    named = run(["packs", "get", "broken"])
    qualified = run(["get", "broken/other"])

    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.stdout)["ref"] == "good/playbook"
    for result in (named, qualified):
        assert result.exit_code != 0
        assert "broken" in result.stderr
        assert "line" in result.stderr


def test_path_ref_helper_classifies_dot_forms() -> None:
    for value in (".", "..", "./x", "../x", "/abs", "~/x", "~"):
        assert is_path_ref(value), value
    for value in ("pack", "pack/recipe", "x.yml", ".hidden"):
        assert not is_path_ref(value), value


def test_unified_list_recipes_hooks_and_packs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook/recipe.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    install_pack(source)

    recipes = run(["list", "--format", "json"])
    hooks = run(["hooks", "list", "--format", "json"])
    packs = run(["packs", "list", "--format", "json"])

    assert recipes.exit_code == 0, recipes.output
    assert json.loads(recipes.stdout) == [
        {
            "pack": "ansible",
            "name": "playbook",
            "ref": "ansible/playbook",
            # One recipe.recipe schema: what only ``recipe get`` reads is null here.
            "description": None,
            "inputs": None,
            "steps": None,
            "hooks": None,
            "path": str(library_dir() / "packs" / "ansible" / "recipes/playbook/recipe.yml"),
        }
    ]
    # Library hooks list before the built-ins.
    assert [row["ref"] for row in json.loads(hooks.stdout)] == ["ansible/check", "yaml_edit"]
    assert json.loads(packs.stdout)[0]["name"] == "ansible"


@pytest.mark.parametrize("args", [["list"], ["packs", "list"], ["validate"]])
def test_empty_library_prints_guidance(tmp_path: Path, args: list[str]) -> None:
    table = run(args)
    as_json = run([*args, "--format", "json"])

    assert table.exit_code == 0, table.output
    assert table.stdout == ""
    assert "no packs installed" in table.stderr
    assert "untaped recipe packs init NAME" in table.stderr
    assert "add" in table.stderr
    assert as_json.exit_code == 0, as_json.output
    # validate without a ref never enumerates the built-ins.
    assert json.loads(as_json.stdout) == []
    assert "no packs installed" in as_json.said


@pytest.mark.parametrize("shadow", ["pack", "recipe"])
def test_check_prefers_library_refs_over_builtin(tmp_path: Path, shadow: str) -> None:
    source = tmp_path / "source"
    if shadow == "pack":
        write_pack(source, manifest_name="yaml_edit", recipes={"playbook": "recipes/p.yml"})
        install_pack(source, name="yaml_edit")
    else:
        write_pack(source, manifest_name="shadow", recipes={"yaml_edit": "recipes/yaml.yml"})
        install_pack(source)

    result = run(["validate", "yaml_edit", "--format", "json"])

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert (row["type"], row["status"], row["detail"]) == (shadow, "pass", None)
    assert "error" not in row
    if shadow == "pack":
        assert (row["name"], row["path"]) == ("yaml_edit", str(library_dir() / "packs/yaml_edit"))
    else:
        assert (row["name"], row["path"]) == (
            "shadow/yaml_edit",
            str(library_dir() / "packs" / "shadow" / "recipes/yaml.yml"),
        )


@pytest.mark.parametrize(("hooks", "probes"), [({"check": "ansible_pack.hooks.check"}, 1), ({}, 0)])
def test_check_probes_lock_freshness_once_per_hook_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hooks: dict[str, str],
    probes: int,
) -> None:
    source = tmp_path / "source"
    recipes = {name: f"recipes/{name}/recipe.yml" for name in ("one", "two", "three")}
    write_pack(source, manifest_name="ansible", recipes=recipes, hooks=hooks)
    install_pack(source)
    probed: list[Path] = []
    monkeypatch.setattr("untaped_recipe.infrastructure.pack_files.check_lock", probed.append)

    result = run(["validate", "ansible", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(probed) == probes


def test_check_hookless_pack_without_lock_passes_every_ref_form(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_pack(source, manifest_name="plain", recipes={"ok": "recipes/ok.yml"})
    install_pack(source)
    installed = library_dir() / "packs" / "plain"
    (installed / "uv.lock").unlink()
    (source / "uv.lock").unlink()
    pack_row = {
        "name": "plain",
        "type": "pack",
        "status": "pass",
        "path": str(installed),
        "detail": None,
    }

    def validate(*ref: str) -> list[dict[str, object]]:
        result = run(["validate", *ref, "--format", "json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    assert validate("plain") == [pack_row]
    assert validate() == [pack_row]
    assert validate("plain/ok") == [
        {
            "name": "plain/ok",
            "type": "recipe",
            "status": "pass",
            "path": str(installed / "recipes/ok.yml"),
            "detail": None,
        }
    ]
    assert validate(str(source)) == [{**pack_row, "path": str(source)}]


@pytest.mark.parametrize(
    ("ref", "make_dir", "expected", "hint"),
    [
        ("demo", True, "recipe not found: 'demo'", "a path named 'demo' exists"),
        ("demo", False, "recipe not found: 'demo'", None),
        ("foo bar", False, "not found: 'foo bar'", None),
    ],
)
@pytest.mark.parametrize("command", ["get", "edit", "validate", "test"])
def test_library_miss_hints_only_at_an_existing_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    ref: str,
    make_dir: bool,
    expected: str,
    hint: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    if make_dir:
        (tmp_path / ref).mkdir()

    result = run([command, ref])

    assert result.exit_code == 1
    assert expected in result.stderr
    assert "safe library name" not in result.stderr
    if hint is None:
        assert "a path named" not in result.stderr
    else:
        assert hint in result.stderr
        assert "prefix ./" in result.stderr
