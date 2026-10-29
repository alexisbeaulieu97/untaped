"""Finding what a library command names: a pack, a recipe, a hook, or a pack to scaffold into."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from untaped.sdk import hint, not_found
from untaped_recipe.application.resolution import find_library_recipe
from untaped_recipe.builtins.registry import BUILTIN_HOOKS
from untaped_recipe.domain.pack import HookEntry, InstalledPack, RecipeEntry, parse_ref
from untaped_recipe.domain.paths import is_path_ref, safe_library_name
from untaped_recipe.errors import (
    AmbiguousRefError,
    HookNotFoundError,
    PackNotFoundError,
    RecipeNotFoundError,
)
from untaped_recipe.infrastructure.pack_store import PackLibrary

if TYPE_CHECKING:
    from untaped_recipe.commands._recipe import RecipeCommand

#: What every scaffolding command warns after ``--no-lock``.
NO_LOCK_NOTE = "uv.lock was not created/refreshed for {path}; hooks need `uv lock` before running"


@dataclass(frozen=True)
class ResolvedHook:
    """A hook ref resolved for ``hooks get``/``hooks edit``: a pack hook or a built-in."""

    name: str
    pack: InstalledPack | None = None
    hook: HookEntry | None = None


def find_pack(library: PackLibrary, name: str) -> InstalledPack:
    pack = library.find_pack(name)
    if pack is None:
        raise PackNotFoundError(
            not_found("pack", name, known=sorted(pack.name for pack in library.packs()))
        )
    return pack


def find_recipe(
    library: PackLibrary, ref_text: str, *, verb: Literal["get", "edit"]
) -> tuple[InstalledPack, str, RecipeEntry]:
    """A library recipe; a miss that names a pack or hook instead says which command reads it."""
    try:
        return find_library_recipe(library, ref_text)
    except RecipeNotFoundError as exc:
        if (noun := _other_noun(library, ref_text)) is None:
            raise
        raise RecipeNotFoundError(
            f"{exc}\n{hint(_instead(noun, verb).spelled(ref_text))}"
        ) from None


def _instead(noun: str, verb: Literal["get", "edit"]) -> type[RecipeCommand]:
    """The ``packs`` or ``hooks`` command doing ``verb`` (``get``, ``edit``) for a missed recipe."""
    from untaped_recipe.commands import hooks, packs  # noqa: PLC0415 - they import this module

    if noun == "packs":
        return packs.Get if verb == "get" else packs.Edit
    return hooks.Get if verb == "get" else hooks.Edit


def _other_noun(library: PackLibrary, ref_text: str) -> str | None:
    """``packs``/``hooks`` when a missed recipe ref names a pack or hook instead."""
    if library.find_pack(ref_text) is not None:
        return "packs"
    try:
        find_hook(library, ref_text)
    except AmbiguousRefError:
        return "hooks"
    except HookNotFoundError, ValueError:
        return None
    return "hooks"


def find_hook(library: PackLibrary, ref_text: str) -> ResolvedHook:
    """Resolve a hook ref: an installed pack's hook first, then a bare built-in name."""
    ref = parse_ref(ref_text)
    try:
        pack, hook = library.find_hook(ref)
    except HookNotFoundError:
        if "/" not in ref_text and ref_text in BUILTIN_HOOKS:
            return ResolvedHook(name=ref_text)
        raise
    return ResolvedHook(name=ref.name, pack=pack, hook=hook)


def pack_child(library: PackLibrary, ref_text: str) -> tuple[Path, str]:
    """The pack directory and name a ``PACK/NAME`` (or ``./path/NAME``) scaffold ref names."""
    if is_path_ref(ref_text):
        path = Path(ref_text).expanduser()
        if not path.name or path.parent == Path("."):
            raise ValueError("qualified refs must use <pack>/<name>")
        return path.parent, path.name
    ref = parse_ref(ref_text)
    if ref.pack is None:
        raise ValueError("qualified refs must use <pack>/<name>")
    installed = library.find_pack(safe_library_name(ref.pack, field="pack"))
    if installed is not None:
        return installed.root, ref.name
    there = ""
    if Path(ref.pack).is_dir():
        there = (
            f" (a directory named '{ref.pack}' exists — use ./{ref.pack}/{ref.name}, "
            f"or install it with packs add ./{ref.pack})"
        )
    raise PackNotFoundError(not_found("pack", ref.pack) + there)
