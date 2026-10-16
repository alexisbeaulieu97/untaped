"""Tests for the recipe, pack and hook records ``list`` and ``get`` share."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from untaped.testing import CliInvoker
from untaped_recipe.cli import app
from untaped_recipe.cli.common import library_dir
from untaped_recipe.cli.detail import (
    hook_record,
    pack_record,
    recipe_record,
    table_recipe_detail,
)
from untaped_recipe.domain.pack import InstalledPack, PackManifest, RecipeEntry
from untaped_recipe.infrastructure.pack_files import read_pack_manifest

pytestmark = pytest.mark.usefixtures("isolate_config")


def _write_detail_pack(root: Path) -> None:
    recipe_path = root / "recipes" / "playbook" / "recipe.yml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    (recipe_path.parent / "templates").mkdir()
    (recipe_path.parent / "templates" / "config.yml").write_text("owner: {{ owner }}\n")
    recipe_path.write_text(
        "version: 1\n"
        "description: |\n"
        "  Configure ownership.\n"
        "  Extra detail stays out of pack summaries.\n"
        "inputs:\n"
        "  owner:\n"
        "    type: str\n"
        "    required: true\n"
        "    description: Owner name\n"
        "  token:\n"
        "    type: str\n"
        "    sensitive: true\n"
        "steps:\n"
        "  - type: template\n"
        "    template: templates/config.yml\n"
        "    dest: config.yml\n"
        "  - type: transform\n"
        "    file: config.yml\n"
        "    hook: set_owner\n"
        "  - type: validate\n"
        "    hook: check_owner\n",
        encoding="utf-8",
    )
    hooks_dir = root / "src" / "ansible_pack" / "hooks"
    hooks_dir.mkdir(parents=True)
    (root / "src" / "ansible_pack" / "__init__.py").write_text("", encoding="utf-8")
    (hooks_dir / "__init__.py").write_text("", encoding="utf-8")
    (hooks_dir / "set_owner.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    return content\n"
        "def validate(*, inputs, target, args, helpers):\n"
        "    return helpers.pass_()\n",
        encoding="utf-8",
    )
    (hooks_dir / "check_owner.py").write_text(
        "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
        encoding="utf-8",
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-ansible"\n'
        'version = "0.2.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"playbook" = { path = "recipes/playbook/recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"set_owner" = { module = "ansible_pack.hooks.set_owner" }\n'
        '"check_owner" = { module = "ansible_pack.hooks.check_owner" }\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def test_recipe_detail_lists_inputs_steps_and_hooks(tmp_path: Path) -> None:
    _write_detail_pack(tmp_path)
    recipe_path = tmp_path / "recipes" / "playbook" / "recipe.yml"
    pack = InstalledPack.local(tmp_path, read_pack_manifest(tmp_path))

    detail = recipe_record(pack, "playbook", detail=True).model_dump(mode="json")
    row = recipe_record(pack, "playbook", detail=False).model_dump(mode="json")

    assert (detail["pack"], detail["name"], detail["ref"]) == (
        "ansible",
        "playbook",
        "ansible/playbook",
    )

    assert detail["inputs"] == [
        {
            "name": "owner",
            "type": "str",
            "required": True,
            "default": None,
            "description": "Owner name",
            "sensitive": False,
        },
        {
            "name": "token",
            "type": "str",
            "required": False,
            "default": None,
            "description": "",
            "sensitive": True,
        },
    ]
    assert detail["steps"] == [
        {"type": "template", "files": ["config.yml"], "globs": [], "exclude": [], "hook": ""},
        {
            "type": "transform",
            "files": ["config.yml"],
            "globs": [],
            "exclude": [],
            "hook": "set_owner",
        },
        {"type": "validate", "files": [], "globs": [], "exclude": [], "hook": "check_owner"},
    ]
    assert detail["hooks"] == ["check_owner", "set_owner"]
    assert detail["path"] == str(recipe_path)
    # The list row is the same record without what only get reads.
    assert row == detail | dict.fromkeys(["description", "inputs", "steps", "hooks"])


def test_hook_detail_reports_ast_exports(tmp_path: Path) -> None:
    _write_detail_pack(tmp_path)
    pack = InstalledPack.local(tmp_path, read_pack_manifest(tmp_path))
    module_file = tmp_path / "src" / "ansible_pack" / "hooks" / "set_owner.py"

    detail = hook_record(pack, "set_owner", detail=True)

    assert hook_record(pack, "set_owner", detail=False).exports is None
    assert detail.model_dump(mode="json") == {
        "pack": "ansible",
        "name": "set_owner",
        "ref": "ansible/set_owner",
        "module": "ansible_pack.hooks.set_owner",
        "exports": ["transform", "validate"],
        "path": str(module_file),
    }


def test_pack_detail_lists_recipe_summaries_and_hook_exports(tmp_path: Path) -> None:
    _write_detail_pack(tmp_path)
    manifest = read_pack_manifest(tmp_path)

    pack = InstalledPack(
        name="alias",
        root=tmp_path,
        manifest=manifest,
        source="https://example.com/acme/pack.git",
        rev="v1",
        installed_version="0.1.0",
        commit="abc123",
    )

    detail = pack_record(pack, detail=True).model_dump(mode="json")
    row = pack_record(pack, detail=False).model_dump(mode="json")

    assert list(detail) == [
        "name",
        "project",
        "version",
        "path",
        "source",
        "rev",
        "commit",
        "recipes",
        "hooks",
        "manifest_name",
    ]
    assert detail["name"] == "alias"
    assert detail["manifest_name"] == manifest.name
    assert (detail["source"], detail["rev"], detail["commit"]) == (
        "https://example.com/acme/pack.git",
        "v1",
        "abc123",
    )
    assert detail["project"] == "untaped-recipe-ansible"
    assert detail["version"] == "0.2.0"
    assert detail["recipes"] == [{"name": "playbook", "description": "Configure ownership."}]
    assert {"name": "set_owner", "exports": ["transform", "validate"]} in detail["hooks"]
    assert detail["path"] == str(tmp_path)
    # packs list counts what packs get summarises, and gives the installed version.
    assert row == detail | {"version": "0.1.0", "recipes": 1, "hooks": 2}


def test_show_recipe_cli_emits_structured_recipe_record(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_detail_pack(source)
    library = library_dir()
    (library / "packs").mkdir(parents=True)
    shutil.copytree(source, library / "packs" / "ansible")

    result = CliInvoker().invoke(app, ["get", "ansible/playbook", "--format", "json"])

    assert result.exit_code == 0, result.output
    detail = json.loads(result.stdout)
    assert detail["ref"] == "ansible/playbook"
    assert (detail["pack"], detail["name"]) == ("ansible", "playbook")
    assert detail["inputs"][0]["name"] == "owner"
    assert detail["inputs"][0]["required"] is True


def test_recipe_detail_describes_fanout_and_glob_steps(tmp_path: Path) -> None:
    recipe_path = tmp_path / "recipe.yml"
    recipe_path.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    files: [a.yml, b.yml]\n"
        "    hook: edit\n"
        "  - type: remove\n"
        "    globs: ['**/*.bak']\n"
        "    exclude: [keep.bak]\n",
        encoding="utf-8",
    )

    manifest = PackManifest(
        name="acme",
        project_name="acme-pack",
        version="1.0",
        recipes={"demo": RecipeEntry(path="recipe.yml")},
        hooks={},
    )
    record = recipe_record(InstalledPack.local(tmp_path, manifest), "demo", detail=True)
    detail = record.model_dump(mode="json")

    assert detail["steps"] == [
        {
            "type": "transform",
            "files": ["a.yml", "b.yml"],
            "globs": [],
            "exclude": [],
            "hook": "edit",
        },
        {"type": "remove", "files": [], "globs": ["**/*.bak"], "exclude": ["keep.bak"], "hook": ""},
    ]
    assert table_recipe_detail(record)["steps"] == (
        "transform a.yml,b.yml hook=edit; remove globs=**/*.bak exclude=keep.bak"
    )
