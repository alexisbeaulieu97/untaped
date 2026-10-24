"""The version of installed code: what ``Cache(version=)`` keys a value by.

``installed_code(*modules)`` names the code a cache's value depends on and
returns the function the cache calls for its version. With modules, the
version is a hash of their files, so editing one of them changes it and
nothing else does: ansible's resources cache follows its parsers. Without,
it is the whole install: untaped and every distribution that offers an
untaped plugin, each by its version and a hash of its ``RECORD`` (a reinstall
at the same version changes it) and, for an editable install, by the files
under its source root, walked by modification time and size. That is what
core's composed-plugins cache follows, so an edit to any plugin rebuilds it on
the next run. Neither form imports a module to find it, and each computes once
per process.

Two installs the whole-install form cannot see into: a development install
with neither ``RECORD`` nor ``direct_url.json`` (``setup.py develop``) counts
by its version alone, and files a plugin reads from outside its source root
(its ``src`` folder when it has one) never count. A named top-level package
an import hook installs (a setuptools editable finder) is found once it is
imported, not before.
"""

from __future__ import annotations

import functools
import hashlib
import os
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from untaped.sdk.errors import ConfigError

if TYPE_CHECKING:
    import importlib.metadata

#: The entry point group a distribution offers untaped plugins under.
_PLUGIN_GROUP = "untaped.plugins"
#: Folders a walk never enters, besides hidden ones: bytecode and other tools' trees.
_SKIPPED_FOLDERS = frozenset({"__pycache__", "node_modules"})


@dataclass(frozen=True)
class InstalledCode:
    """The version of some installed code; calling it computes (once) its hash."""

    modules: tuple[str, ...] = ()

    def __call__(self) -> str:
        if self.modules:
            return _modules_version(self.modules)
        return _install_version()

    def __repr__(self) -> str:
        return f"installed_code({', '.join(map(repr, self.modules))})"


def installed_code(*modules: ModuleType | str) -> InstalledCode:
    """The version of ``modules``' files, or of the whole install when none is named.

    A module is the module itself or its dotted name; a package counts every
    file under it. ``Cache(version=installed_code(parsers))`` refreshes when
    the parsers change; ``Cache(version=installed_code())`` when any plugin
    is installed, upgraded, reinstalled or edited.
    """
    names: list[str] = []
    for module in modules:
        if isinstance(module, ModuleType):
            names.append(module.__name__)
        elif isinstance(module, str) and module:
            names.append(module)
        else:
            raise TypeError(f"installed_code: {module!r} is not a module or a module's name")
    return InstalledCode(tuple(names))


# --------------------------------------------------------------------------- #
# Named modules: their files' bytes
# --------------------------------------------------------------------------- #


@functools.cache
def _modules_version(modules: tuple[str, ...]) -> str:
    parts: list[bytes] = []
    for module in modules:
        parts.append(f"module:{module}".encode())
        for root, files in _module_files(module):
            for path in files:
                content = _read(path, module)
                if content is not None:
                    parts.append(f"file:{path.relative_to(root).as_posix()}".encode())
                    parts.append(content)
    return _hash(parts)


def _read(path: Path, module: str) -> bytes | None:
    """``path``'s bytes; ``None`` when it went away while walking (an editor's save)."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError(
            f"installed_code: cannot read {path} of module {module!r}: {exc.strerror or exc}"
        ) from exc


def _module_files(module: str) -> Iterator[tuple[Path, list[Path]]]:
    """Each folder or file ``module`` is made of, with its files in a stable order."""
    for root in _module_roots(module):
        if root.is_dir():
            yield root, sorted(_walk(root))
        else:
            yield root.parent, [root]


def _module_roots(module: str) -> list[Path]:
    """Where ``module``'s files are on disk, found the way an import would, without one.

    A loaded module is read from itself; otherwise each package on the way
    is searched in its parent's folders (a loaded parent's ``__path__``), so
    namespace packages and import-hook installs resolve as Python does.
    """
    loaded = sys.modules.get(module)
    if loaded is not None:
        paths = getattr(loaded, "__path__", None)
        found = list(paths) if paths is not None else [getattr(loaded, "__file__", None)]
    else:
        found = _found_roots(module)
    roots = [Path(each) for each in found if isinstance(each, str)]
    if not roots or not all(root.is_dir() or root.is_file() for root in roots):
        raise ConfigError(
            f"installed_code: module {module!r} has no source files on disk to hash "
            "(not found, or loaded from an archive)"
        )
    return roots


def _found_roots(module: str) -> list[str | None]:
    """``module``'s folders or file, searched as the path finder would, importing nothing.

    Each name is looked for in its parent's folders (``sys.path`` for the top
    one, a loaded parent's ``__path__``): the first regular package or module
    wins, and a name found only as folders is a namespace package of them all.
    """
    from importlib.machinery import all_suffixes  # noqa: PLC0415 - read when a version is asked for

    search: list[str] = [entry for entry in sys.path if isinstance(entry, str)]
    parts = module.split(".")
    for index, part in enumerate(parts):
        loaded = sys.modules.get(".".join(parts[: index + 1]))
        if loaded is not None and index < len(parts) - 1:
            search = [
                each for each in getattr(loaded, "__path__", None) or [] if isinstance(each, str)
            ]
            continue
        portions: list[str] = []
        found: list[str | None] | None = None
        for entry in search:
            folder = os.path.join(entry or ".", part)
            if os.path.isfile(os.path.join(folder, "__init__.py")):
                found = [folder]
                break
            file = next(
                (folder + suffix for suffix in all_suffixes() if os.path.isfile(folder + suffix)),
                None,
            )
            if file is not None:
                found = [file]
                break
            if os.path.isdir(folder):
                portions.append(folder)
        if found is None:
            found = list(portions)
        if not found:
            return []
        if index < len(parts) - 1:
            if not os.path.isdir(str(found[0])):  # a module has no submodules
                return []
            search = [str(each) for each in found]
        else:
            return found
    return []


# --------------------------------------------------------------------------- #
# The whole install: every plugin distribution, and untaped
# --------------------------------------------------------------------------- #


@functools.cache
def _install_version() -> str:
    import importlib.metadata  # noqa: PLC0415 - read when a version is asked for

    found: dict[str, importlib.metadata.Distribution] = {}
    try:
        found["untaped"] = importlib.metadata.distribution("untaped")
    except importlib.metadata.PackageNotFoundError:  # untaped run from a source tree
        untaped = None
    else:
        untaped = found["untaped"]
    for entry_point in importlib.metadata.entry_points(group=_PLUGIN_GROUP):
        if entry_point.dist is not None:
            found.setdefault(entry_point.dist.name, entry_point.dist)
    parts: list[bytes] = []
    if untaped is None:
        parts.extend(_stat_parts("untaped", *_module_roots("untaped")))
    for name in sorted(found):
        parts.extend(_distribution_parts(name, found[name]))
    return _hash(parts)


def _distribution_parts(name: str, distribution: importlib.metadata.Distribution) -> list[bytes]:
    parts = [f"distribution:{name}=={distribution.version}".encode()]
    record = distribution.read_text("RECORD")
    parts.append(b"record:" + (record or "").encode())
    root = _editable_root(distribution)
    if root is not None:
        parts.extend(_stat_parts(name, root))
    return parts


def _stat_parts(name: str, *roots: Path) -> list[bytes]:
    """Each file under ``roots`` by path, modification time and size."""
    parts: list[bytes] = []
    for root in roots:
        files = sorted(_walk(root)) if root.is_dir() else [root]
        for path in files:
            try:
                stat = path.stat()
            except OSError:  # gone while walking
                continue
            where = path.relative_to(root).as_posix() if root.is_dir() else path.name
            parts.append(f"source:{name}:{where}:{stat.st_mtime_ns}:{stat.st_size}".encode())
    return parts


def _editable_root(distribution: importlib.metadata.Distribution) -> Path | None:
    """The source root of an editable install (``direct_url.json``), its ``src`` when it has one."""
    import json  # noqa: PLC0415 - read when a version is asked for
    from urllib.parse import unquote, urlparse  # noqa: PLC0415 - as above

    text = distribution.read_text("direct_url.json")
    if not text:
        return None
    try:
        direct = json.loads(text)
    except ValueError:
        return None
    if not isinstance(direct, dict) or not (direct.get("dir_info") or {}).get("editable"):
        return None
    url = urlparse(str(direct.get("url", "")))
    if url.scheme != "file":
        return None
    root = Path(unquote(url.path))
    return root / "src" if (root / "src").is_dir() else root


# --------------------------------------------------------------------------- #
# Shared
# --------------------------------------------------------------------------- #


def _walk(root: Path) -> Iterable[Path]:
    """Every file under ``root`` that is code or data, not a tool's.

    Hidden files and folders, bytecode, editor backups (``x~``, ``#x#``),
    ``node_modules`` and virtual environments (a folder with ``pyvenv.cfg``)
    are left out, so an open editor or a stray environment changes nothing.
    """
    for folder, subfolders, files in root.walk():
        subfolders[:] = [
            each
            for each in subfolders
            if not each.startswith(".")
            and each not in _SKIPPED_FOLDERS
            and not (folder / each / "pyvenv.cfg").exists()
        ]
        for name in files:
            if name.startswith((".", "#")) or name.endswith(("~", ".pyc", ".pyo")):
                continue
            yield folder / name


def _hash(parts: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:  # length-prefixed: no two lists of parts hash alike
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()
