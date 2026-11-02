"""Every SDK name has a first-party consumer, or says who its first consumer will be.

The primitives guardrail (§7): a name in ``untaped.sdk`` or one of its groups
is imported by a first-party plugin (core's own commands included) or an
example, or carries ``@experimental`` and a docstring line "First consumer:
...", or is in the transition family the clean slate leaves without a 12.0
user, whose line reads "First consumer: the 12 to 13 transition". A name
that stops being used fails here in the PR that stops using it, and its
author deletes it or writes the consumer down.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Iterable
from pathlib import Path

from repo.support import REPO_ROOT
from untaped.sdk.marks import Experimental, function_mark

#: The SDK's root and the groups a plugin imports from (``testing`` serves tests).
SDK_GROUPS = (
    "untaped.sdk",
    "untaped.sdk.storage",
    "untaped.sdk.network",
    "untaped.sdk.health",
    "untaped.sdk.git",
    "untaped.sdk.process",
    "untaped.sdk.projects",
    "untaped.sdk.prompt",
    "untaped.sdk.contracts",
)

#: The deprecation plumbing and setting renames: no 12.0 user, kept for the next major.
TRANSITION = frozenset(
    {
        "Deprecated",
        "DeprecatedBehaviour",
        "UntapedDeprecationWarning",
        "deprecated",
        "deprecated_parameters",
        "Renamed",
        "Retired",
    }
)
TRANSITION_LINE = "First consumer: the 12 to 13 transition."


def consumer_roots() -> list[Path]:
    """Where first-party consumers live: each plugin package, the examples, core's own plugin."""
    return [
        *sorted((REPO_ROOT / "packages").glob("untaped-*")),
        REPO_ROOT / "examples",
        REPO_ROOT / "packages/untaped/src/untaped/core",
    ]


def imported_names(roots: Iterable[Path]) -> set[tuple[str, str]]:
    """``(module, name)`` for each ``from <sdk group> import name`` under ``roots``."""
    found: set[tuple[str, str]] = set()
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module in SDK_GROUPS:
                    found.update((node.module, alias.name) for alias in node.names)
    return found


def unaccounted(
    module: str, names: Iterable[str], objects: dict[str, object], used: set[tuple[str, str]]
) -> list[str]:
    """The ``names`` of ``module`` with no consumer, no "First consumer" line and no family."""
    missing = []
    for name in names:
        if (module, name) in used:
            continue
        doc = inspect.getdoc(objects[name]) or ""
        if name in TRANSITION and TRANSITION_LINE in doc:
            continue
        if isinstance(function_mark(objects[name]), Experimental) and "First consumer:" in doc:
            continue
        missing.append(f"{module}.{name}")
    return missing


def test_every_sdk_name_has_a_consumer_or_names_its_first() -> None:
    used = imported_names(consumer_roots())
    missing = []
    for path in SDK_GROUPS:
        module = importlib.import_module(path)
        objects = {name: getattr(module, name) for name in module.__all__}
        missing += unaccounted(path, module.__all__, objects, used)
    assert missing == [], (
        "no first-party plugin or example imports these, and none says who will: "
        "delete it, make it private, or mark it @experimental with a 'First consumer:' line"
    )


def test_an_unused_stable_name_is_refused_and_a_named_consumer_passes() -> None:
    from untaped.sdk.marks import experimental

    def stable() -> None:
        """A helper nobody imports."""

    @experimental
    def ahead() -> None:
        """A helper ahead of its user.

        First consumer: the acme plugin.
        """

    objects = {"stable": stable, "ahead": ahead, "Retired": type("Retired", (), {})}
    names = ["stable", "ahead", "Retired"]

    assert unaccounted("m", names, objects, set()) == ["m.stable", "m.Retired"]
    assert unaccounted("m", names, objects, {("m", "stable"), ("m", "Retired")}) == []
