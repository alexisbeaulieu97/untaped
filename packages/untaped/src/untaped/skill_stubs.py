"""Stub skills: core's version-neutral template and the marker recording who wrote one.

A stub skill names what a plugin manages and sends the agent to
``untaped agents``; it lists no command, so one stub serves every untaped
on the machine. Its marker, ``.untaped-stub.json``, records two versions
because the stub has two owners: core's template writes the body and the
plugin writes the description. :func:`compare` is the one rule every reader
(the per-run check, ``skills update``, ``skills status``, ``doctor``) uses
to decide whether a run is newer than the stub it finds.

untaped 11.0 counts only directories with ``.untaped-skill.json`` (a copy),
so it never sees a stub and never writes a copy back over one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib import metadata
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

#: The marker file in a stub's directory; a copy's is ``.untaped-skill.json``.
STUB_MARKER = ".untaped-stub.json"
#: What ``skills list`` shows as a stub's ``source``: the template's identity.
STUB_SOURCE = "untaped:stub/1"
#: The marker file's own layout, read by every later version.
MARKER_FORMAT = 1
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
    """This run's untaped version; empty when the distribution is not installed."""
    try:
        return metadata.version(CORE)
    except metadata.PackageNotFoundError:
        return ""


def compare(written: StubAuthor, run: StubAuthor) -> Comparison:
    """Whether ``run`` is newer than, the same as, or older than ``written``.

    A run is newer when its untaped or its plugin version is newer and
    neither is older; older when either is older. The plugin side counts as
    equal when it can't be compared: another distribution (a fork, a rename,
    a plugin moved to its own repo), an unknown version, or one that doesn't
    parse. Versions order by PEP 440.
    """
    sides = [_order(run.untaped, written.untaped)]
    if canonicalize_name(run.plugin) == canonicalize_name(written.plugin):
        sides.append(_order(run.plugin_version, written.plugin_version))
    if any(side < 0 for side in sides):
        return Comparison.older
    if any(side > 0 for side in sides):
        return Comparison.newer
    return Comparison.same


def newer_side(written: StubAuthor, run: StubAuthor) -> str:
    """Name what wrote a stub that ``run`` is older than: ``untaped (11.3.0)`` or the plugin."""
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

    Only the frozen forms of ``untaped agents`` appear in it, so every stub
    ever installed keeps working.
    """
    core = plugin == CORE
    title = CORE if core else f"untaped {plugin}"
    about = "its core commands" if core else f"its {plugin} plugin"
    what = (
        "core's commands and settings"
        if core
        else ("this plugin's commands, settings, and the plugins that extend it")
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
`untaped agents` lists the plugins it has.

1. `untaped agents`: read it first. It names the version that answered, how untaped works, and
   every installed plugin, so you can combine them.
2. `untaped agents {plugin}`: {what}.
3. `untaped agents {plugin} COMMAND`: a command's options and examples. Read it before running
   the command.

If that untaped answers `agents` with "Unknown command", it is older than this skill: use
`{fallback}` instead.
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


def marker_text(
    author: StubAuthor, *, sha256: str, name: str, target: str, scope: str, install_root: Path
) -> str:
    """The marker file's contents."""
    return (
        json.dumps(
            {
                "format": MARKER_FORMAT,
                "untaped": author.untaped,
                "plugin": author.plugin,
                "plugin_version": author.plugin_version,
                "sha256": sha256,
                "name": name,
                "target": target,
                "scope": scope,
                "install_root": str(install_root),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def read_marker(directory: Path) -> StubMarker | None:
    """The stub marker in ``directory``; ``None`` when missing or unreadable."""
    try:
        data = json.loads((directory / STUB_MARKER).read_text(encoding="utf-8"))
        return StubMarker(
            author=StubAuthor(
                untaped=str(data["untaped"]),
                plugin=str(data["plugin"]),
                plugin_version=str(data.get("plugin_version") or ""),
            ),
            sha256=str(data["sha256"]),
            target=str(data["target"]),
            scope=str(data["scope"]),
        )
    except OSError, ValueError, TypeError, KeyError:
        return None
