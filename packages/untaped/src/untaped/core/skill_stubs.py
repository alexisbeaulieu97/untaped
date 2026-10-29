"""Stub skills: core's version-neutral template and the marker recording who wrote one.

A stub skill names what a plugin manages and sends the agent to
``untaped docs``; it lists no command, so one stub serves every untaped
on the machine. Its marker, ``.untaped-stub.json``, is a data file
(:mod:`untaped.sdk.storage.data_file`) recording two versions because the stub has two
owners: core's template writes the body (the stamp's ``by`` names its
untaped) and the plugin writes the description (``plugin_version``).
:func:`compare` is the one rule every reader (the per-run check, ``skills
update``, ``skills status``, ``doctor``) uses to decide whether a run is
newer than the stub it finds.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel

#: The marker file in a stub's directory.
STUB_MARKER = ".untaped-stub.json"
#: The distribution whose version is the untaped side of a stub.
CORE = "untaped"

#: The stub's only file besides the marker.
SKILL_FILE = "SKILL.md"


@dataclass(frozen=True)
class StubAuthor:
    """The two versions a stub records: untaped's and its plugin's.

    ``plugin`` is the plugin's distribution; ``plugin_version`` is empty when
    unknown (an entry point with no distribution).
    """

    untaped: str
    plugin: str
    plugin_version: str


@dataclass(frozen=True)
class StubMarker:
    """A stub marker as read from disk."""

    author: StubAuthor
    sha256: str
    target: str
    scope: str


class Comparison(StrEnum):
    """How a run compares with the run that wrote a stub."""

    newer = "newer"
    same = "same"
    older = "older"


def running_untaped() -> str:
    """This run's untaped version, the one a marker's stamp names."""
    from untaped.sdk.storage import data_file  # noqa: PLC0415 - first use

    return data_file.written_by()


def compare(written: StubAuthor, run: StubAuthor) -> Comparison:
    """Whether ``run`` is newer than, the same as, or older than ``written``.

    A run is newer when its untaped or its plugin version is newer and
    neither is older, and older the other way round; a run newer on one side
    and older on the other is the same (the stub reads as current). The plugin side counts as
    equal when it can't be compared: another distribution (a fork, a rename,
    a plugin moved to its own repo), an unknown version, or one that doesn't
    parse. Versions order by PEP 440.
    """
    sides = [_order(run.untaped, written.untaped)]
    if canonicalize_name(run.plugin) == canonicalize_name(written.plugin):
        sides.append(_order(run.plugin_version, written.plugin_version))
    newer, older = any(side > 0 for side in sides), any(side < 0 for side in sides)
    if newer and not older:
        return Comparison.newer
    if older and not newer:
        return Comparison.older
    return Comparison.same


def newer_side(written: StubAuthor, run: StubAuthor) -> str:
    """Name what wrote a stub that ``run`` is older than: ``untaped (12.3.0)`` or the plugin."""
    if _order(run.untaped, written.untaped) < 0:
        return f"untaped ({written.untaped})"
    return f"{written.plugin} ({written.plugin_version})"


def _order(left: str, right: str) -> int:
    try:
        first, second = Version(left), Version(right)
    except InvalidVersion:
        return 0
    return (first > second) - (first < second)


def render_stub(name: str, plugin: str, description: str) -> str:
    """The stub's ``SKILL.md`` for ``plugin`` (``untaped`` for core).

    Only the command forms its test allows appear in it, so a release that
    changes ``untaped docs`` knows exactly what the stubs depend on.
    """
    core = plugin == CORE
    title = CORE if core else f"untaped {plugin}"
    about = "its core commands" if core else f"its {plugin} plugin"
    what = (
        "core's sections: its commands, the rows they print and its settings"
        if core
        else "what to read first about it, and its sections: commands, files, rows and settings"
    )
    fallback = "untaped --help" if core else f"untaped {plugin} --help"
    # A JSON string is a valid YAML double-quoted scalar.
    return f"""---
name: {name}
description: {json.dumps(description, ensure_ascii=False)}
---

# {title}

untaped is a command line on this machine; this skill is about {about}. Its commands and
options depend on the installed version, so this skill lists none of them: ask the untaped you
will run.

Which untaped: in a project whose pyproject.toml names untaped, the project's own, run through
the project's environment (for example `uv run untaped`); elsewhere, `untaped` on PATH.
`untaped docs` lists the plugins it has.

1. `untaped docs`: read it first. It names the version that answered, how its pages are
   organized, and every installed plugin, so you can combine them.
2. `untaped docs {plugin}`: {what}.
3. `untaped docs {plugin} commands COMMAND`: a command's options and examples. Read it before
   running the command.

If that untaped answers `docs` with "Unknown command", use `{fallback}` instead.
"""


def stub_files(name: str, plugin: str, description: str) -> dict[str, bytes]:
    """Every file a stub holds besides its marker, by relative path."""
    return {SKILL_FILE: render_stub(name, plugin, description).encode()}


def digest(files: Mapping[str, bytes]) -> str:
    """One sha256 over relative paths and contents, so an added file counts as an edit."""
    hasher = hashlib.sha256()
    for path in sorted(files):
        hasher.update(path.encode() + b"\0" + hashlib.sha256(files[path]).digest())
    return hasher.hexdigest()


class _MarkerFile(BaseModel):
    """The marker's fields; the untaped side of its author is the stamp's ``by``."""

    plugin: str
    plugin_version: str = ""
    sha256: str
    name: str = ""
    target: str
    scope: str
    install_root: str = ""


def write_marker(
    directory: Path,
    *,
    plugin: str,
    plugin_version: str,
    sha256: str,
    name: str,
    target: str,
    scope: str,
    install_root: Path,
) -> None:
    """Write the stub marker in ``directory``, stamped by this run's untaped."""
    from untaped.sdk.storage.data_file import write_data_file  # noqa: PLC0415 - first use

    write_data_file(
        directory / STUB_MARKER,
        _MarkerFile(
            plugin=plugin,
            plugin_version=plugin_version,
            sha256=sha256,
            name=name,
            target=target,
            scope=scope,
            install_root=str(install_root),
        ),
    )


def read_marker(directory: Path) -> StubMarker | None:
    """The stub marker in ``directory``; ``None`` when missing or unreadable (damaged)."""
    from untaped.sdk.storage.data_file import Found, read_data_file  # noqa: PLC0415 - first use

    read = read_data_file(directory / STUB_MARKER, _MarkerFile)
    if not isinstance(read, Found):
        return None
    marker = read.value
    return StubMarker(
        author=StubAuthor(
            untaped=read.by or "",
            plugin=marker.plugin,
            plugin_version=marker.plugin_version,
        ),
        sha256=marker.sha256,
        target=marker.target,
        scope=marker.scope,
    )
