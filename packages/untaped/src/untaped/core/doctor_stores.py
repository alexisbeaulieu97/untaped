"""``untaped doctor``'s rows about what untaped keeps on disk.

The repo stores, the caches, the plugins' folders and the references
projects and plugins hold, each with the fix that tidies it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from untaped.core.doctor_row import PASS, WARN, doctor_row
from untaped.core.settings import NAME
from untaped.runtime.profile_resolver import fix_target
from untaped.runtime.registry import CompositionResult
from untaped.sdk.words import (
    command_argv,
    plural,
    shown_path,
    size_text,
)

if TYPE_CHECKING:
    from untaped.sdk.storage._refs import Reference, RefHolder
    from untaped.sdk.storage.store import Layout, Store


def stores_row(result: CompositionResult) -> dict[str, object]:
    """Each composed store's layouts on disk and who holds each.

    Green while every layout on disk is held or will go at the next complete
    fill; yellow for one the cleanup cannot remove, with the reason.
    """
    from untaped.sdk.storage._refs import read_references  # noqa: PLC0415 - runs only in doctor
    from untaped.sdk.storage.store import Store, survey  # noqa: PLC0415

    title = "stores keep one layout each, or every layout is held"
    stores = [
        store
        for plugin in result.plugins
        for store in plugin.stores
        if isinstance(store, Store)  # an item store keeps no layouts
    ]
    if not stores:
        return doctor_row("stores", NAME, PASS, title, "no plugin declares a store")
    references = read_references()
    parts: list[str] = []
    reasons: set[str] = set()
    for store in stores:
        try:
            path, layouts = store.path(), survey(store, references)
        except Exception as exc:
            parts.append(f"{store.qualified}: could not find its files: {exc}")
            reasons.add("files")
            continue
        texts: list[str] = []
        if not any(layout.own for layout in layouts):
            texts.append(f"layout {store.layout} at {shown_path(path)}, not created yet")
        for layout in layouts:
            text, warns = _layout_text(store, layout, references)
            texts.append(text)
            reasons |= warns
        parts.append(f"{store.qualified}: {'; '.join(texts)}")
    if not reasons:
        return doctor_row("stores", NAME, PASS, title, "; ".join(parts))
    return doctor_row("stores", NAME, WARN, title, "; ".join(parts), _stores_fix(reasons))


def caches_row(result: CompositionResult) -> dict[str, object]:
    """What doctor notes about caches: always a pass, since every cache refills.

    A cache's keys holding only another version's value. The caches of a
    plugin not installed here are in its folder's row (:func:`plugin_folders_row`).
    """
    from untaped.sdk.storage.cache import outdated_keys  # noqa: PLC0415 - runs only in doctor

    title = "no caches of another version"
    parts: list[str] = []
    for plugin in result.plugins:
        for cache in plugin.caches:
            try:
                outdated = outdated_keys(cache)
            except Exception as exc:  # the plugin's version= or model= code runs here
                parts.append(f"{cache.qualified}: could not check its files: {exc}")
                continue
            if outdated:
                keys = plural(outdated, "key", "keys")
                parts.append(
                    f"{cache.qualified}: {keys} filled by another version, refilled when due"
                )
    return doctor_row("caches", NAME, PASS, title, "; ".join(parts) or "nothing to note")


def plugin_folders_row(result: CompositionResult) -> dict[str, object]:
    """Each ``plugins/<name>/`` folder of a plugin not installed here, offered to ``setup prune``.

    It warns while one could go (no live reference names its plugin): it
    goes whole, state included, so only a person removes it. One that stays
    (a symlink, a plugin a live reference names, any reference unreadable)
    is listed with why.
    """
    from untaped.core.commands.setup_prune import leftover_folders  # noqa: PLC0415
    from untaped.sdk.storage._fs import dir_bytes  # noqa: PLC0415 - runs only in doctor
    from untaped.sdk.storage._refs import read_references  # noqa: PLC0415

    title = "no folders of plugins not installed here"
    leftovers = leftover_folders(result, read_references())
    if not leftovers:
        return doctor_row("plugin-folders", NAME, PASS, title, "nothing to note")
    parts = []
    for leftover in leftovers:
        text = f"{shown_path(leftover.path)}: plugin {leftover.path.name!r} isn't installed here"
        if leftover.kept is not None:
            parts.append(f"{text}; kept: {leftover.kept}")
        else:
            size = size_text(dir_bytes(leftover.path))
            parts.append(f"{text} and nothing here uses it ({size}); it goes whole, state included")
    if all(leftover.kept is not None for leftover in leftovers):
        return doctor_row("plugin-folders", NAME, PASS, title, "; ".join(parts))
    return doctor_row("plugin-folders", NAME, WARN, title, "; ".join(parts), _prune_fix())


def _prune_fix() -> list[str]:
    """``setup prune``'s argv, spelled from its class.

    Never automatic: what it removes may be a disk not mounted now, or a
    plugin's state, so a person decides.
    """
    from untaped.core.commands.setup_prune import Prune  # noqa: PLC0415 - setup imports these rows

    return command_argv(Prune.spelled(), profile=fix_target())


def _register_fix() -> list[str]:
    """``setup register``'s argv, spelled from its class; never automatic (a deliberate step)."""
    from untaped.core.commands.register import Register  # noqa: PLC0415 - setup imports these rows

    return command_argv(Register.spelled(), profile=fix_target())


def _layout_text(
    store: Store, layout: Layout, references: Sequence[Reference]
) -> tuple[str, set[str]]:
    """One layout as the ``stores`` row says it, and the codes of what it warns about.

    A damaged in-use lock is said even when unknown holders are the reason
    the cleanup keeps the layout.
    """
    from untaped.sdk.storage._fs import dir_bytes  # noqa: PLC0415 - runs only in doctor
    from untaped.sdk.storage._refs import this_install  # noqa: PLC0415
    from untaped.sdk.storage.store import _damaged, kept_reason  # noqa: PLC0415

    size = size_text(dir_bytes(layout.path))
    text = f"layout {layout.layout} at {shown_path(layout.path)} ({size})"
    reason = kept_reason(store, layout, references)
    me = this_install()
    if layout.holders:
        text += ", held by " + ", ".join(
            "this install" if me and ref.holder == me.holder else ref.describe()
            for ref in layout.holders
        )
    elif not layout.own and reason is not None and not reason.unheld:
        text += ", holders unknown"
    elif not layout.own:
        text += ", held by nobody"
        if not reason:
            text += " (removed at the next complete fill)"
    found = [reason] if reason else []
    damaged = _damaged(layout)
    if damaged is not None and reason is not None and reason.code != "damaged":
        found.append(damaged)
    if found:
        text += ": " + ", and ".join(kept.text for kept in found)
    return text, {kept.code for kept in found}


def _stores_fix(reasons: set[str]) -> list[str] | None:
    """The ``stores`` row's fix for what keeps its layouts: register, or none.

    Never automatic: registering is a deliberate step.
    """
    if "unregistered" in reasons:
        return _register_fix()
    return None


def references_row(result: CompositionResult) -> dict[str, object]:
    """Every reference in the home, with its state, and whether this install is registered.

    Installs hold store layouts; projects hold items. A missing project
    warns; an unreadable one (its drive or share away) is listed, not warned.
    An install that is not reachable warns, its fix ``setup prune``, unless
    this install needs registering first.
    """
    from untaped.core.commands.setup_prune import dead_references  # noqa: PLC0415
    from untaped.sdk.storage._refs import (  # noqa: PLC0415 - runs only in doctor
        REFS,
        environment,
        install_holder,
        read_references,
    )

    title = "references"
    references = read_references()
    env = environment()
    mine = install_holder(env) if env is not None else None
    described = [_reference_text(ref, mine) for ref in references]
    parts = [text for text, _ in described]
    warn = any(warns for _, warns in described)
    registered = any(ref.opaque is None and ref.holder == mine for ref in references)
    # a dead reference is removed by a person: another install's registration is never a run's
    fix = _prune_fix() if dead_references(references) else None
    if env is None:
        parts.append("this run is from a source tree: it cannot be registered")
    elif not registered and not any(ref.file == REFS.place for ref in references):
        if _other_layouts(result, references):
            warn = True
            parts.insert(0, "this install is not registered: nothing cleans up old store layouts")
            fix = _register_fix()
        else:
            parts.insert(0, "this install: not registered")
    detail = "; ".join(parts) or "no install is registered"
    # Never automatic: a lasting registration is a deliberate step (a one-off `uvx` run's
    # environment would otherwise pin layouts until its installer's cache is cleared).
    return doctor_row("references", NAME, WARN if warn else PASS, title, detail, fix)


def _reference_text(ref: Reference, mine: RefHolder | None) -> tuple[str, bool]:
    """How the ``references`` row names one reference, and whether it warns."""
    from untaped.sdk.storage._refs import (  # noqa: PLC0415 - runs only in doctor
        INSTALL,
        PROJECT,
        REFS,
    )

    if ref.opaque is not None:
        if "newer" in ref.opaque:
            advice = "; upgrade this install to prune"
        elif ref.file == REFS.place:
            advice = ", so nothing is pruned; check its permissions"
        else:
            advice = ", so nothing is pruned; delete it"
        return f"{shown_path(ref.file)}: {ref.opaque}{advice}", True
    if ref.kind == PROJECT:
        return _project_text(ref)
    text = ref.describe()
    if ref.kind != INSTALL and ref.plugin:
        text += f" (plugin {ref.plugin})"
    if ref.holder == mine:
        text += " (this install)"
    if not ref.live:
        return f"{text}: not reachable", True
    if ref.pre_upgrade:
        text += ": reference from before an upgrade; run any command from that install"
        text += " to refresh it"
    return text, False


def _project_text(ref: Reference) -> tuple[str, bool]:
    """How the ``references`` row names a project, and whether it warns (missing, repair due)."""
    from untaped.sdk.projects import anchor_state  # noqa: PLC0415 - runs only in doctor

    where = shown_path(ref.anchor) if ref.anchor is not None else "?"
    state = anchor_state(ref)
    if state == "missing":
        return f"{ref.describe()}: {where} is gone; it keeps what it holds until removed", True
    if state == "unreadable":
        return f"{ref.describe()}: {where} can't be read now (a drive or share away?)", False
    if ref.moved_from:
        was = ", ".join(shown_path(path) for path in ref.moved_from)
        return (
            f"{ref.describe()} at {where}: moved from {was}, and a plugin's "
            "repair is still pending; the next command that changes the project retries it",
            True,
        )
    return f"{ref.describe()} at {where}", False


def _other_layouts(result: CompositionResult, references: Sequence[Reference]) -> bool:
    """Whether any composed store has a layout on disk besides its own."""
    from untaped.sdk.storage.store import Store, survey  # noqa: PLC0415 - runs only in doctor

    for plugin in result.plugins:
        for store in plugin.stores:
            if not isinstance(store, Store):
                continue
            try:
                layouts = survey(store, references)
            except Exception:
                continue
            if any(not layout.own for layout in layouts):
                return True
    return False
