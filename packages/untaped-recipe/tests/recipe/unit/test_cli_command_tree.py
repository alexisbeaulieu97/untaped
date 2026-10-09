"""The ``untaped recipe`` command tree: recipe verbs plus ``packs``/``hooks``/``backups`` nouns."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cyclopts import App

from untaped.testing import CliInvoker
from untaped_recipe.cli import app
from untaped_recipe.cli.common import library_dir
from untaped_recipe.infrastructure.pack_store import PackLibrary

pytestmark = pytest.mark.usefixtures("isolate_config")


def _commands(group: App) -> set[str]:
    return {name for name in group if not name.startswith("-") and group[name].show is not False}


def _write_pack(root: Path) -> None:
    """A pack ``acme`` with recipe ``editorconfig`` and validate hook ``probe``."""
    recipe = root / "recipes" / "editorconfig" / "recipe.yml"
    recipe.parent.mkdir(parents=True)
    recipe.write_text("version: 1\nsteps: []\n")
    hook = root / "src" / "acme_pack" / "hooks" / "probe.py"
    hook.parent.mkdir(parents=True)
    (root / "src" / "acme_pack" / "__init__.py").write_text("")
    (hook.parent / "__init__.py").write_text("")
    hook.write_text("def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "acme"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.8,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"editorconfig" = { path = "recipes/editorconfig/recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"probe" = { module = "acme_pack.hooks.probe" }\n'
    )
    (root / "uv.lock").write_text("version = 1\n")


def _install(tmp_path: Path) -> None:
    source = tmp_path / "acme"
    _write_pack(source)
    PackLibrary(library_dir=library_dir()).add(
        source, source=str(source), rev=None, name=None, force=False
    )


def _default_columns(stderr: str) -> set[str]:
    """The columns ``--columns ?`` marks as shown in a table by default."""
    return {line.split()[0] for line in stderr.splitlines() if line.endswith(" *")}


def test_recipe_verbs_stay_at_the_top_and_nouns_group_the_rest() -> None:
    assert _commands(app) == {
        "apply",
        "backups",
        "edit",
        "get",
        "hooks",
        "init",
        "list",
        "packs",
        "test",
        "validate",
    }
    assert _commands(app["packs"]) == {"add", "edit", "get", "init", "list", "remove", "sync"}
    assert _commands(app["hooks"]) == {"edit", "get", "init", "list", "run"}
    assert _commands(app["backups"]) == {"get", "list", "prune", "restore"}


def test_each_noun_lists_gets_and_edits_its_own_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path)
    invoker = CliInvoker()

    recipes = invoker.invoke(app, ["list", "--format", "json"])
    packs = invoker.invoke(app, ["packs", "list", "--format", "json"])
    hooks = invoker.invoke(app, ["hooks", "list", "--format", "json"])
    recipe = invoker.invoke(app, ["get", "acme/editorconfig", "--format", "json"])
    pack = invoker.invoke(app, ["packs", "get", "acme", "--format", "json"])
    hook = invoker.invoke(app, ["hooks", "get", "acme/probe", "--format", "json"])
    builtin = invoker.invoke(app, ["hooks", "get", "yaml_edit", "--format", "json"])

    assert [row["ref"] for row in json.loads(recipes.stdout)] == ["acme/editorconfig"]
    assert [row["name"] for row in json.loads(packs.stdout)] == ["acme"]
    assert [row["ref"] for row in json.loads(hooks.stdout)] == ["acme/probe", "yaml_edit"]
    assert json.loads(recipe.stdout)["ref"] == "acme/editorconfig"
    assert json.loads(pack.stdout)["name"] == "acme"
    assert json.loads(hook.stdout)["ref"] == "acme/probe"
    assert json.loads(builtin.stdout)["ref"] == "yaml_edit"

    opened: list[Path] = []
    monkeypatch.setattr(
        "untaped_recipe.cli.library_commands.run_editor",
        lambda path, **_: opened.append(path),
    )
    for argv in (["edit", "acme/editorconfig"], ["packs", "edit", "acme"]):
        assert invoker.invoke(app, argv).exit_code == 0
    assert invoker.invoke(app, ["hooks", "edit", "acme/probe"]).exit_code == 0
    pack_root = library_dir() / "packs" / "acme"
    assert opened == [
        pack_root / "recipes" / "editorconfig" / "recipe.yml",
        pack_root / "pyproject.toml",
        pack_root / "src" / "acme_pack" / "hooks" / "probe.py",
    ]


_HINT = "\nhint: run `untaped recipe {noun} {verb} {ref}`"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        pytest.param(
            ["get", "acme"],
            "recipe not found: 'acme'" + _HINT.format(noun="packs", verb="get", ref="acme"),
            id="recipe-get-pack",
        ),
        pytest.param(
            ["edit", "acme/probe"],
            "recipe not found: 'acme/probe'"
            + _HINT.format(noun="hooks", verb="edit", ref="acme/probe"),
            id="recipe-edit-hook",
        ),
        pytest.param(
            ["get", "yaml_edit"],
            "recipe not found: 'yaml_edit'"
            + _HINT.format(noun="hooks", verb="get", ref="yaml_edit"),
            id="recipe-get-builtin",
        ),
        pytest.param(["get", "nothing"], "recipe not found: 'nothing'\n", id="no-hint"),
        pytest.param(
            ["packs", "get", "acme/editorconfig"],
            "pack not found: 'acme/editorconfig'",
            id="packs-get-recipe",
        ),
        pytest.param(
            ["hooks", "get", "acme/editorconfig"],
            "hook not found: 'acme/editorconfig'",
            id="hooks-get-recipe",
        ),
        pytest.param(
            ["init", "acme"],
            "recipe refs must use <pack>/<recipe>\nhint: run `untaped recipe packs init acme`",
            id="init-pack-name",
        ),
    ],
)
def test_nouns_do_not_resolve_each_others_refs(
    tmp_path: Path, argv: list[str], message: str
) -> None:
    _install(tmp_path)

    result = CliInvoker().invoke(app, argv)

    assert result.exit_code == 1, result.output
    assert message in result.stderr


def test_init_scaffolds_each_noun_from_its_own_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    invoker = CliInvoker()

    pack = invoker.invoke(app, ["packs", "init", "acme", "--no-lock"])
    recipe = invoker.invoke(app, ["init", "./acme/editorconfig", "--no-lock"])
    hook = invoker.invoke(app, ["hooks", "init", "./acme/probe", "--kind", "validate", "--no-lock"])

    assert pack.exit_code == 0, pack.output
    assert (tmp_path / "acme" / "pyproject.toml").is_file()
    assert recipe.exit_code == 0, recipe.output
    assert Path(recipe.stdout.strip()).name == "recipe.yml"
    assert hook.exit_code == 0, hook.output
    assert "scaffolded validate hook" in hook.stderr
    pyproject = (tmp_path / "acme" / "pyproject.toml").read_text()
    assert 'path = "recipes/editorconfig/recipe.yml"' in pyproject
    assert 'module = "acme_pack.hooks.probe"' in pyproject


def test_packs_list_pipe_composes_into_sync_and_remove(tmp_path: Path) -> None:
    _install(tmp_path)
    invoker = CliInvoker()
    packs = invoker.invoke(app, ["packs", "list", "--format", "pipe"]).stdout
    recipes = invoker.invoke(app, ["list", "--format", "pipe"]).stdout

    synced = invoker.invoke(app, ["packs", "sync", "--stdin", "--format", "json"], input=packs)
    wrong_kind = invoker.invoke(app, ["packs", "remove", "--stdin", "--yes"], input=recipes)
    removed = invoker.invoke(
        app, ["packs", "remove", "--stdin", "--yes", "--format", "json"], input=packs
    )

    assert synced.exit_code == 0, synced.output
    assert [(row["name"], row["action"]) for row in json.loads(synced.stdout)] == [
        ("acme", "unchanged")
    ]
    assert wrong_kind.exit_code == 2, wrong_kind.output
    assert removed.exit_code == 0, removed.output
    assert [(row["name"], row["action"]) for row in json.loads(removed.stdout)] == [
        ("acme", "removed")
    ]
    assert not (library_dir() / "packs" / "acme").exists()


@pytest.mark.parametrize(
    ("argv", "defaults"),
    [
        pytest.param(["list"], {"pack", "name"}, id="recipes"),
        pytest.param(
            ["packs", "list"],
            {"name", "version", "source", "rev", "recipes", "hooks"},
            id="packs",
        ),
        pytest.param(["hooks", "list"], {"pack", "name", "module"}, id="hooks"),
    ],
)
def test_list_tables_leave_out_refs_paths_and_commits(
    tmp_path: Path, argv: list[str], defaults: set[str]
) -> None:
    _install(tmp_path)

    result = CliInvoker().invoke(app, [*argv, "--columns", "?"])

    assert result.exit_code == 0, result.output
    assert _default_columns(result.stderr) == defaults
