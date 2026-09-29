"""``recipe init``, ``recipe packs init`` and ``recipe hooks init``: scaffold projects."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter

from untaped.capabilities.recipe.cli._context import recipe_ui
from untaped.capabilities.recipe.cli.common import library_root, report_config_errors
from untaped.capabilities.recipe.domain.pack import parse_ref
from untaped.capabilities.recipe.domain.paths import is_path_ref, safe_library_name
from untaped.capabilities.recipe.errors import PackNotFoundError
from untaped.capabilities.recipe.infrastructure import pack_scaffold
from untaped.capabilities.recipe.infrastructure.pack_store import PackLibrary
from untaped.capability_api import echo, hint

_NO_LOCK_NOTE = "uv.lock was not created/refreshed for {path}; hooks need `uv lock` before running"

_LockOption = Annotated[
    bool,
    Parameter(
        name="--lock",
        negative="--no-lock",
        help="Refresh uv.lock after scaffolding (--no-lock skips it).",
    ),
]


def init_pack_command(
    name: Annotated[str, Parameter(help="Pack NAME, created under the current directory.")],
    /,
    *,
    lock: _LockOption = True,
) -> None:
    """Scaffold a recipe pack project."""
    with report_config_errors():
        pack_name = safe_library_name(name, field="pack")
        path = pack_scaffold.scaffold_pack(Path.cwd() / pack_name, pack_name, lock=lock)
        if not lock:
            _warn_no_lock(path)
        echo(str(path))


def init_recipe_command(
    ref: Annotated[str, Parameter(help="PACK/RECIPE reference (or ./path/RECIPE).")],
    /,
    *,
    lock: _LockOption = True,
) -> None:
    """Scaffold a recipe, with a starter golden test case, inside a pack."""
    with report_config_errors():
        if "/" not in ref:
            raise ValueError(
                f"recipe refs must use <pack>/<recipe>\n{hint(f'recipe packs init {ref}')}"
            )
        pack_dir, name = _new_pack_child(ref)
        path = pack_scaffold.scaffold_recipe(pack_dir, name, lock=lock)
        if not lock:
            _warn_no_lock(pack_dir)
        echo(str(path))


def init_hook_command(
    ref: Annotated[str, Parameter(help="PACK/HOOK reference (or ./path/HOOK).")],
    /,
    *,
    kind: Annotated[
        Literal["transform", "validate"],
        Parameter(name="--kind", help="Hook callable stub kind."),
    ] = "transform",
    force: Annotated[
        bool,
        Parameter(
            name="--force",
            negative="",
            help="Replace an existing hook's stub and paired test, e.g. wrong --kind.",
        ),
    ] = False,
    lock: _LockOption = True,
) -> None:
    """Scaffold a hook module and its pytest inside a pack."""
    with report_config_errors():
        pack_dir, name = _new_pack_child(ref)
        path = pack_scaffold.scaffold_hook(pack_dir, name, kind=kind, lock=lock, force=force)
        if not lock:
            _warn_no_lock(pack_dir)
        recipe_ui().message(
            "info",
            f"scaffolded {kind} hook (choose with --kind transform|validate; "
            "replace an existing hook with --force)",
        )
        echo(str(path))


def _warn_no_lock(project_root: Path) -> None:
    recipe_ui().message("warning", _NO_LOCK_NOTE.format(path=project_root))


def _new_pack_child(ref_text: str) -> tuple[Path, str]:
    if is_path_ref(ref_text):
        path = Path(ref_text).expanduser()
        if not path.name or path.parent == Path("."):
            raise ValueError("qualified refs must use <pack>/<name>")
        return path.parent, path.name
    ref = parse_ref(ref_text)
    if ref.pack is None:
        raise ValueError("qualified refs must use <pack>/<name>")
    library = PackLibrary(library_root=library_root())
    installed = library.find_pack(safe_library_name(ref.pack, field="pack"))
    if installed is not None:
        return installed.root, ref.name
    raise PackNotFoundError(f"pack not found: {ref.pack}{_new_pack_child_hint(ref.pack, ref.name)}")


def _new_pack_child_hint(pack: str, name: str) -> str:
    if Path(pack).is_dir():
        return (
            f" (a directory named '{pack}' exists — use ./{pack}/{name}, "
            f"or install it with packs add ./{pack})"
        )
    return ""
