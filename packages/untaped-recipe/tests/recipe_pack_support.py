"""The packs, hook projects and pack libraries the recipe command tests write."""

from __future__ import annotations

import subprocess
from pathlib import Path

from recipe_test_support import library_dir
from untaped_recipe.infrastructure.pack_store import PackLibrary


def default_columns(stderr: str) -> set[str]:
    """The columns ``--columns ?`` marks as shown in a table by default."""
    return {line.split()[0] for line in stderr.splitlines() if line.endswith(" *")}


def write_hook_project(
    root: Path,
    *,
    public_name: str,
    module_name: str,
    code: str,
    package: str = "recipe_hooks",
) -> None:
    module_path = root / "src" / package / "hooks" / f"{module_name}.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    (root / "src" / package / "__init__.py").write_text("")
    (root / "src" / package / "hooks" / "__init__.py").write_text("")
    module_path.write_text(code)
    recipe_metadata = (
        '[tool.untaped_recipe.recipes]\n"demo" = { path = "recipe.yml" }\n\n'
        if (root / "recipe.yml").is_file()
        else ""
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{root.name}-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        f"{recipe_metadata}"
        "[tool.untaped_recipe.hooks]\n"
        f'"{public_name}" = {{ module = "{package}.hooks.{module_name}" }}\n'
    )
    subprocess.run(["uv", "lock"], cwd=root, check=True)


def write_pack_project(root: Path) -> None:
    hook_module = root / "src" / "demo_hooks" / "hooks" / "check.py"
    hook_module.parent.mkdir(parents=True, exist_ok=True)
    (root / "src" / "demo_hooks" / "__init__.py").write_text("")
    (root / "src" / "demo_hooks" / "hooks" / "__init__.py").write_text("")
    hook_module.write_text(
        "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n"
    )
    recipe = root / "recipes" / "demo" / "recipe.yml"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("version: 1\nsteps: []\n")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-demo"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.8,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"demo-recipe" = { path = "recipes/demo/recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"demo_hook" = { module = "demo_hooks.hooks.check" }\n'
    )
    (root / "uv.lock").write_text("version = 1\n")


def write_pack(
    root: Path,
    *,
    manifest_name: str,
    recipes: dict[str, str],
    hooks: dict[str, str] | None = None,
    recipe_body: str = "version: 1\nsteps: []\n",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    recipe_rows: list[str] = []
    for name, relative in recipes.items():
        recipe_path = root / relative
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        recipe_path.write_text(recipe_body, encoding="utf-8")
        recipe_rows.append(f'"{name}" = {{ path = "{relative}" }}')
    hook_rows: list[str] = []
    for name, module in (hooks or {}).items():
        module_path = root / "src" / Path(*module.split(".")).with_suffix(".py")
        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(
            "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
            encoding="utf-8",
        )
        package = root / "src" / Path(module.split(".")[0])
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        hooks_package = root / "src" / Path(*module.split(".")[:-1])
        (hooks_package / "__init__.py").write_text("", encoding="utf-8")
        hook_rows.append(f'"{name}" = {{ module = "{module}" }}')
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-{manifest_name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.8,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        + "\n".join(recipe_rows)
        + "\n"
        + ("\n[tool.untaped_recipe.hooks]\n" + "\n".join(hook_rows) + "\n" if hook_rows else ""),
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def install_pack(source: Path, *, name: str | None = None) -> None:
    PackLibrary(library_dir=library_dir()).add(
        source,
        source=str(source),
        rev=None,
        name=name,
        force=False,
    )
