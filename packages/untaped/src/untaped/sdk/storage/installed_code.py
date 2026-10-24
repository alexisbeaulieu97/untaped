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
"""

from __future__ import annotations

import functools
import hashlib
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


@functools.cache
def _modules_version(modules: tuple[str, ...]) -> str:
    parts: list[bytes] = []
    for module in modules:
        for root, files in _module_files(module):
            parts.append(f"module:{module}".encode())
            for path in files:
                parts.append(f"file:{path.relative_to(root).as_posix()}".encode())
                parts.append(path.read_bytes())
    return _hash(parts)


def _module_files(module: str) -> Iterator[tuple[Path, list[Path]]]:
    """Each folder or file ``module`` is made of, with its files in a stable order."""
    for root in _module_roots(module):
        if root.is_dir():
            yield root, sorted(_walk(root))
        else:
            yield root.parent, [root]


def _module_roots(module: str) -> list[Path]:
    """Where ``module``'s files are, found on ``sys.path`` without importing it."""
    import sys  # noqa: PLC0415 - read when a version is asked for

    loaded = sys.modules.get(module)
    if loaded is not None:
        paths = getattr(loaded, "__path__", None)
        if paths is not None:
            return [Path(path) for path in paths]
        if getattr(loaded, "__file__", None):
            return [Path(loaded.__file__)]  # type: ignore[arg-type]
    *parents, last = module.split(".")
    for entry in sys.path:
        folder = Path(entry or ".").joinpath(*parents)
        if (folder / last / "__init__.py").is_file():
            return [folder / last]
        if (folder / f"{last}.py").is_file():
            return [folder / f"{last}.py"]
    raise ConfigError(f"installed_code: module {module!r} has no source files to hash")


@functools.cache
def _install_version() -> str:
    import importlib.metadata  # noqa: PLC0415 - read when a version is asked for

    found: dict[str, importlib.metadata.Distribution] = {}
    try:
        found["untaped"] = importlib.metadata.distribution("untaped")
    except importlib.metadata.PackageNotFoundError:  # untaped run from a source tree
        pass
    for entry_point in importlib.metadata.entry_points(group=_PLUGIN_GROUP):
        if entry_point.dist is not None:
            found.setdefault(entry_point.dist.name, entry_point.dist)
    parts: list[bytes] = []
    for name in sorted(found):
        parts.extend(_distribution_parts(name, found[name]))
    return _hash(parts)


def _distribution_parts(name: str, distribution: importlib.metadata.Distribution) -> list[bytes]:
    parts = [f"distribution:{name}=={distribution.version}".encode()]
    record = distribution.read_text("RECORD")
    parts.append(b"record:" + (record or "").encode())
    root = _editable_root(distribution)
    if root is not None:
        for path in sorted(_walk(root)):
            try:
                stat = path.stat()
            except OSError:  # gone while walking
                continue
            parts.append(
                f"source:{path.relative_to(root).as_posix()}:{stat.st_mtime_ns}:{stat.st_size}".encode()
            )
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


def _walk(root: Path) -> Iterable[Path]:
    """Every file under ``root``, leaving out hidden folders and bytecode."""
    for folder, subfolders, files in root.walk():
        subfolders[:] = [s for s in subfolders if not s.startswith(".") and s != "__pycache__"]
        yield from (folder / name for name in files if not name.endswith((".pyc", ".pyo")))


def _hash(parts: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:  # length-prefixed: no two lists of parts hash alike
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()
