"""Where each plugin's refs live in a store repo, and which refs a call covers.

A plugin's refs live under ``refs/untaped/<plugin>/heads/<branch>`` and
``refs/untaped/<plugin>/tags/<tag>``. One exception is declared here and
nowhere else: workspace's refs are the plain-clone layout,
``refs/remotes/origin/*`` for heads and ``refs/tags/*`` for tags, so
``origin/main``, ``@{upstream}`` and a tag checkout work in its worktrees as
in a clone. Its tags are never pruned (a user's unpushed local tag survives),
and ``refs/remotes/origin/HEAD``, the symref the store writes for it, is never
a fetched ref.

Callers name branches and tags, never refspecs: a name is a ref path below
``refs/heads/`` or ``refs/tags/`` with at most one ``*``, checked here before
any git runs. Refs are named to callers relative to the layout's roots,
``heads/<branch>`` and ``tags/<tag>``, whatever the layout.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: Plugins whose refs use the plain-clone layout (see the module docstring).
PLAIN_CLONE = frozenset({"workspace"})
#: The symref the store writes for the plain-clone layout; never pruned.
ORIGIN_HEAD = "refs/remotes/origin/HEAD"

# Refused anywhere in a name: refspec syntax, revision syntax, git's ref rules.
_BAD = re.compile(r"[\x00-\x20\x7f:~^?\[\\+]|\.\.|@\{|//|\.lock(/|$)|/\.|^\.|\.$|/$")


@dataclass(frozen=True, slots=True)
class Layout:
    """One plugin's namespace: its two roots and what a prune may delete."""

    plugin: str
    heads: str
    tags: str
    prune_tags: bool

    @property
    def roots(self) -> tuple[str, str]:
        return self.heads, self.tags

    def refspecs(self, kind: str, names: Iterable[str]) -> list[str]:
        """``+refs/<kind>/<name>:<root><name>`` for each name (``kind``: heads or tags)."""
        root = self.heads if kind == "heads" else self.tags
        return [f"+refs/{kind}/{name}:{root}{name}" for name in names]

    def relative(self, ref: str) -> str | None:
        """``heads/<b>`` or ``tags/<t>`` for a full ref in this namespace, else ``None``."""
        if ref == ORIGIN_HEAD and self.heads == "refs/remotes/origin/":
            return None
        if ref.startswith(self.heads):
            return f"heads/{ref.removeprefix(self.heads)}"
        if ref.startswith(self.tags):
            return f"tags/{ref.removeprefix(self.tags)}"
        return None

    def absolute(self, relative: str) -> str:
        """The full ref of ``heads/<b>`` or ``tags/<t>``."""
        kind, _, name = relative.partition("/")
        if kind == "heads" and name:
            return f"{self.heads}{name}"
        if kind == "tags" and name:
            return f"{self.tags}{name}"
        raise ValueError(f"{relative!r} is not heads/<branch> or tags/<tag>")

    def prunable(self, relative: str) -> bool:
        """Whether a prune may delete ``relative``."""
        return relative.startswith("heads/") or self.prune_tags


def layout_for(plugin: str) -> Layout:
    """The namespace of ``plugin``'s refs."""
    if plugin in PLAIN_CLONE:
        return Layout(plugin, "refs/remotes/origin/", "refs/tags/", prune_tags=False)
    base = f"refs/untaped/{plugin}/"
    return Layout(plugin, f"{base}heads/", f"{base}tags/", prune_tags=True)


def check_names(names: Sequence[str]) -> str | None:
    """Why ``names`` are not branch or tag names (``None`` when they all are)."""
    for name in names:
        if (
            not name
            or name.startswith(("-", "/", "refs/"))
            or name.count("*") > 1
            or _BAD.search(name)
        ):
            return f"{name!r} is not a branch or tag name (a name or one glob, not a refspec)"
    return None


def is_glob(name: str) -> bool:
    return "*" in name


def covers(name: str, candidate: str) -> bool:
    """Whether the name or glob ``name`` covers the branch or tag ``candidate``.

    A ``*`` matches any run of characters, slashes included, as in a refspec.
    """
    if "*" not in name:
        return name == candidate
    prefix, _, suffix = name.partition("*")
    return (
        len(candidate) >= len(prefix) + len(suffix)
        and candidate.startswith(prefix)
        and candidate.endswith(suffix)
    )


def covered(relative: str, branches: Sequence[str], tags: Sequence[str]) -> bool:
    """Whether ``heads/<b>`` or ``tags/<t>`` is covered by the call's names."""
    kind, _, name = relative.partition("/")
    names = branches if kind == "heads" else tags if kind == "tags" else ()
    return any(covers(each, name) for each in names)
