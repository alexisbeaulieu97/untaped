"""``recipe init``, ``recipe packs init`` and ``recipe hooks init``: scaffold projects."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter

from untaped.sdk import Diagnostics, echo, hint
from untaped_recipe.cli.common import library_dir, report_config_errors
from untaped_recipe.domain.pack import parse_ref
from untaped_recipe.domain.paths import is_path_ref, safe_library_name
from untaped_recipe.errors import PackNotFoundError
from untaped_recipe.infrastructure import pack_scaffold
from untaped_recipe.infrastructure.pack_store import PackLibrary

_diag = Diagnostics()

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
    """Scaffold a recipe pack project.

    A pack is a Python project: its `pyproject.toml` lists recipes under
    `[tool.untaped_recipe.recipes]` and hooks under
    `[tool.untaped_recipe.hooks]`. `untaped[recipe]` stays a dev-only
    dependency; hook runtime dependencies go in `[project].dependencies`.
    Packs ship `pytest` with `pythonpath = ["src"]`, so
    `uv run --project PACK pytest` works at once.

    Each `init` refreshes the pack's `uv.lock`, which needs package-index
    access (or a `[tool.uv.sources]` override). If locking fails, the files
    stay and the error says how to repair them. With --no-lock, hooks can't
    run until `uv lock` succeeds: they run with `uv run --locked --no-dev`.
    How to write the pieces: `untaped docs recipe guides recipe-files` and
    `untaped docs recipe guides writing-hooks`.

    Examples
    --------
    ```console
    $ untaped recipe packs init acme
    $ untaped recipe init ./acme/editorconfig
    $ untaped recipe hooks init ./acme/pin_python --kind validate
    $ untaped recipe test ./acme
    ```
    """
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
    """Scaffold a recipe, with a starter golden test case, inside a pack.

    A `./path/RECIPE` ref targets the pack at `./path`. It locks the pack
    like `packs init`.
    """
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
    """Scaffold a hook module and its pytest inside a pack.

    It writes a typed stub of the --kind callable and a pytest that calls it
    directly. A `./path/HOOK` ref targets the pack at `./path`. An existing
    hook is refused without --force, which replaces both files. It locks the
    pack like `packs init`.
    """
    with report_config_errors():
        pack_dir, name = _new_pack_child(ref)
        path = pack_scaffold.scaffold_hook(pack_dir, name, kind=kind, lock=lock, force=force)
        if not lock:
            _warn_no_lock(pack_dir)
        _diag.info(
            f"scaffolded {kind} hook (choose with --kind transform|validate; "
            "replace an existing hook with --force)",
        )
        echo(str(path))


def _warn_no_lock(project_root: Path) -> None:
    _diag.warning(_NO_LOCK_NOTE.format(path=project_root))


def _new_pack_child(ref_text: str) -> tuple[Path, str]:
    if is_path_ref(ref_text):
        path = Path(ref_text).expanduser()
        if not path.name or path.parent == Path("."):
            raise ValueError("qualified refs must use <pack>/<name>")
        return path.parent, path.name
    ref = parse_ref(ref_text)
    if ref.pack is None:
        raise ValueError("qualified refs must use <pack>/<name>")
    library = PackLibrary(library_dir=library_dir())
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
