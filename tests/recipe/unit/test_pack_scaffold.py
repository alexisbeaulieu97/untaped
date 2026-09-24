"""Tests for unified pack scaffolding."""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

import pytest
from packaging.version import Version

import untaped.capabilities.recipe.infrastructure.pack_scaffold as pack_scaffold
from untaped.capabilities.recipe.application.harness import load_case_spec, orphaned_test_dirs
from untaped.capabilities.recipe.cli import app
from untaped.capabilities.recipe.domain.pack import InstalledPack
from untaped.capabilities.recipe.infrastructure.pack_files import hook_exports, read_pack_manifest
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("isolate_config")


@pytest.fixture(autouse=True)
def no_uv_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    return pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")


def _fail_lock(project_root: Path) -> None:
    raise ValueError("failed to create project uv.lock: mirror is missing untaped-recipe")


def _fail_if_lock_called(project_root: Path) -> None:
    raise AssertionError(f"lock_project should not be called for {project_root}")


def test_scaffold_pack_writes_parseable_manifest_with_hook_api_floors(pack: Path) -> None:
    pyproject = tomllib.loads((pack / "pyproject.toml").read_text(encoding="utf-8"))
    installed = Version(version("untaped"))

    assert read_pack_manifest(pack).name == "ansible"
    assert pyproject["project"]["name"] == "untaped-recipe-ansible"
    assert pyproject["tool"]["untaped_recipe"]["requires_hook_api"] == ">=0.10,<1"
    assert pyproject["dependency-groups"]["dev"] == [
        f"untaped>={installed.public},<{installed.major + 1}",
        "pytest",
    ]
    assert pyproject["tool"]["pytest"]["ini_options"]["pythonpath"] == ["src"]
    assert (pack / "src" / "ansible_pack" / "hooks" / "__init__.py").is_file()


def test_scaffold_recipe_appends_manifest_row_starter_case_and_rejects_duplicates(
    pack: Path,
) -> None:
    recipe_path = pack_scaffold.scaffold_recipe(pack, "playbook")

    assert recipe_path == pack / "recipes" / "playbook" / "recipe.yml"
    assert read_pack_manifest(pack).recipes["playbook"].path == "recipes/playbook/recipe.yml"
    assert "version: 1" in recipe_path.read_text(encoding="utf-8")
    case_dir = pack / "tests" / "playbook" / "basic"
    assert (case_dir / "given").is_dir()
    assert "untaped recipe test <pack>/<recipe>" in (case_dir / "case.yml").read_text()
    assert load_case_spec(case_dir).expect == "success"
    with pytest.raises(ValueError, match="recipe already exists"):
        pack_scaffold.scaffold_recipe(pack, "playbook")


def test_scaffold_recipe_rejects_existing_starter_test_case(pack: Path) -> None:
    (pack / "tests" / "playbook" / "basic").mkdir(parents=True)

    with pytest.raises(ValueError, match="recipe tests already exist: playbook"):
        pack_scaffold.scaffold_recipe(pack, "playbook")


@pytest.mark.parametrize("kind", ["transform", "validate"])
def test_scaffold_hook_writes_exporting_stub_paired_pytest_and_manifest_row(
    pack: Path, kind: str
) -> None:
    module_path = pack_scaffold.scaffold_hook(pack, "probe", kind=kind)

    assert hook_exports(module_path) == frozenset({kind})
    # Pack authors import the stable hook_api path for editor typing.
    assert "from untaped.capabilities.recipe.hook_api import HookHelpers" in (
        module_path.read_text(encoding="utf-8")
    )
    test_path = pack / "tests" / "test_hook_probe.py"
    content = test_path.read_text(encoding="utf-8")
    compile(content, str(test_path), "exec")
    assert f"from ansible_pack.hooks.probe import {kind}" in content
    assert read_pack_manifest(pack).hooks["probe"].module == "ansible_pack.hooks.probe"
    assert "kind" not in (pack / "pyproject.toml").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="hook already exists"):
        pack_scaffold.scaffold_hook(pack, "probe")


def test_scaffold_hook_rejects_existing_test_file(pack: Path) -> None:
    tests_dir = pack / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_hook_set_owner.py").write_text("mine\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hook test already exists"):
        pack_scaffold.scaffold_hook(pack, "set_owner")

    assert not (pack / "src" / "ansible_pack" / "hooks" / "set_owner.py").exists()
    assert (tests_dir / "test_hook_set_owner.py").read_text(encoding="utf-8") == "mine\n"


def test_scaffold_hook_creation_failure_removes_test_file(
    pack: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(pyproject: Path, name: str, module: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pack_scaffold, "_append_hook_row", _boom)
    with pytest.raises(OSError, match="disk full"):
        pack_scaffold.scaffold_hook(pack, "set_owner")

    assert not (pack / "tests").exists()


def test_scaffolded_hook_pack_passes_check(pack: Path) -> None:
    pack_scaffold.scaffold_hook(pack, "set_owner")
    (pack / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    assert orphaned_test_dirs(InstalledPack.local(pack, read_pack_manifest(pack))) == []
    result = CliInvoker().invoke(app, ["validate", str(pack), "--format", "json"])
    assert result.exit_code == 0, result.output


_LOCK_FAILURES: list[tuple[str, Callable[[Path], Path], str, str]] = [
    ("pack", lambda root: pack_scaffold.scaffold_pack(root, "ansible"), "", "recipe pack"),
    (
        "recipe",
        lambda root: pack_scaffold.scaffold_recipe(root, "playbook"),
        "recipes/playbook/recipe.yml",
        "recipe",
    ),
    (
        "hook",
        lambda root: pack_scaffold.scaffold_hook(root, "set_owner"),
        "src/ansible_pack/hooks/set_owner.py",
        "hook module",
    ),
]


@pytest.mark.parametrize(
    ("what", "scaffold", "created", "label"), _LOCK_FAILURES, ids=[row[0] for row in _LOCK_FAILURES]
)
def test_scaffold_lock_failure_keeps_written_files_and_explains_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    what: str,
    scaffold: Callable[[Path], Path],
    created: str,
    label: str,
) -> None:
    root = tmp_path / "ansible"
    if what != "pack":
        pack_scaffold.scaffold_pack(root, "ansible")
    monkeypatch.setattr(pack_scaffold, "lock_project", _fail_lock)

    with pytest.raises(pack_scaffold.ScaffoldLockError) as exc_info:
        scaffold(root)

    message = str(exc_info.value)
    created_path = root / created if created else root
    assert label in message
    assert str(created_path) in message
    assert "mirror is missing untaped-recipe" in message
    assert (
        f"fix the index or add a [tool.uv.sources] override, then run `uv lock` in {root}"
    ) in message
    assert created_path.exists()
    manifest = read_pack_manifest(root)
    assert manifest.recipes.keys() == ({"playbook"} if what == "recipe" else set())
    assert manifest.hooks.keys() == ({"set_owner"} if what == "hook" else set())


@pytest.mark.parametrize(
    ("args", "printed", "created"),
    [
        (["init", "pack", "fresh"], "fresh", "fresh/src/fresh_pack/__init__.py"),
        (
            ["init", "recipe", "./ansible/playbook"],
            "ansible/recipes/playbook/recipe.yml",
            "ansible/tests/playbook/basic/case.yml",
        ),
        (
            ["init", "hook", "./ansible/set_owner"],
            "ansible/src/ansible_pack/hooks/set_owner.py",
            "ansible/tests/test_hook_set_owner.py",
        ),
    ],
)
def test_init_no_lock_never_invokes_uv_and_writes_scaffold(
    tmp_path: Path,
    pack: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    printed: str,
    created: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pack_scaffold, "lock_project", _fail_if_lock_called)

    result = CliInvoker().invoke(app, [*args, "--no-lock"])

    assert result.exit_code == 0, result.output
    assert printed in result.stdout
    assert "uv.lock was not created/refreshed" in result.stderr
    assert "hooks need `uv lock` before running" in result.stderr
    assert (tmp_path / created).is_file()
    assert not (tmp_path / created.split("/")[0] / "uv.lock").exists()


def test_new_hook_explicit_local_path_splits_on_last_segment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    pack_scaffold.scaffold_pack(tmp_path / "some-local-pack", "some-local-pack")

    result = CliInvoker().invoke(app, ["init", "hook", "./some-local-pack/probe"])

    assert result.exit_code == 0, result.output
    manifest = read_pack_manifest(tmp_path / "some-local-pack")
    assert manifest.hooks["probe"].module == "some_local_pack_pack.hooks.probe"


def test_new_hook_names_kind_and_force_replaces_wrong_kind(
    tmp_path: Path,
    pack: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    made = CliInvoker().invoke(app, ["init", "hook", "./ansible/probe"])
    assert made.exit_code == 0, made.output
    assert "scaffolded transform hook" in made.stderr
    assert "--kind" in made.stderr

    refused = CliInvoker().invoke(app, ["init", "hook", "./ansible/probe", "--kind", "validate"])
    assert refused.exit_code != 0
    assert "hook already exists" in refused.output

    forced = CliInvoker().invoke(
        app, ["init", "hook", "./ansible/probe", "--kind", "validate", "--force"]
    )
    assert forced.exit_code == 0, forced.output
    assert "scaffolded validate hook" in forced.stderr
    assert hook_exports(pack / "src" / "ansible_pack" / "hooks" / "probe.py") == {"validate"}
    content = (pack / "tests" / "test_hook_probe.py").read_text(encoding="utf-8")
    assert "import validate" in content
    assert "transform" not in content


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("a/b/c", "qualified refs must use <pack>/<name>"),
        (
            "demo/probe",
            "pack not found: demo (a directory named 'demo' exists — use ./demo/probe, "
            "or install it with add ./demo)",
        ),
        ("missing/probe", "pack not found: missing\n"),
    ],
)
def test_new_hook_rejects_unresolvable_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ref: str,
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "demo").mkdir()

    result = CliInvoker().invoke(app, ["init", "hook", ref])

    assert result.exit_code != 0
    assert message in result.output
