"""Fake record kinds and rule providers for lint's engine and command tests.

Compose them with ``compose_with("lint", provides={"acme": [Acme()]})``: the
``provides`` key is the fake plugin's name, so ``acme``'s ids read
``acme.<id>``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from untaped.sdk import InstalledPlugin, Record
from untaped_lint.api import Finding, Linter, Rule, Rules, Subject, finding, rule


class Widget(Record, kind="linttest.widget"):
    """A record two of acme's rules read."""

    name: str


class Gadget(Record, kind="linttest.gadget"):
    """A record one of gizmo's rules reads."""

    name: str


class Sprocket(Record, kind="linttest.sprocket"):
    """A record no rule reads."""

    name: str


@rule("wide", reads=Widget, severity="error", summary="A widget is narrow.")
def wide(widget: Widget) -> Iterator[Finding]:
    yield finding(wide, widget.name, "too wide")


@rule("tall", reads=Widget, severity="warning", summary="A widget is short.")
def tall(widget: Widget) -> Iterator[Finding]:
    yield finding(tall, widget.name, "too tall")


@rule("boom", reads=Widget, severity="error", summary="A rule that raises.")
def boom(widget: Widget) -> Iterator[Finding]:
    raise RuntimeError(f"cannot read {widget.name}")


@rule(
    "default-branch",
    reads=Gadget,
    severity="error",
    summary="A gadget names its default branch.",
    renamed_from=("old",),
)
def default_branch(gadget: Gadget) -> Iterator[Finding]:
    yield finding(default_branch, gadget.name, "no default branch")


class Counting(Rules):
    """A ``Rules`` host that counts the subjects it is asked about, by kind."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def findings(self, subject: Subject) -> list[Finding]:
        self.asked.append(subject.kind)
        return super().findings(subject)


class Acme(Counting):
    RULES = (wide, tall)


class Gizmo(Counting):
    RULES = (default_branch,)


class Raising(Rules):
    RULES = (wide, boom)


class WarnOnly(Rules):
    RULES = (tall,)


class Malformed(Linter):
    """Declares ``wide`` and answers one valid finding and one with a bad severity."""

    def rules(self) -> list[Rule]:
        return [wide.rule]

    def findings(self, subject: Subject) -> list[Finding]:
        bad = Finding.model_construct(rule="wide", severity="fatal", target="b", message="m")
        return [finding(wide, "a", "too wide"), bad]


class MalformedRule(Linter):
    """Declares a rule whose id is dotted, which no ``Rule`` validates."""

    def rules(self) -> list[Rule]:
        bad = Rule.model_construct(id="acme.dotted", reads=wide.rule.reads, severity="error")
        return [bad]

    def findings(self, subject: Subject) -> list[Finding]:
        return []


@rule("seen", reads=InstalledPlugin, severity="warning", summary="Records each plugin it reads.")
def seen(plugin: InstalledPlugin) -> Iterator[Finding]:
    PluginReader.read.append(plugin)
    yield finding(seen, plugin.name, "seen")


class PluginReader(Rules):
    """A third-party provider of rules over ``untaped.installed_plugin``."""

    RULES = (seen,)
    read: list[InstalledPlugin] = []  # noqa: RUF012 - shared by the rule function, reset per test


def pipe(*records: Record, kind: str | None = "linttest.widget") -> str:
    """``records`` as ``--format pipe`` lines of ``kind`` (``None`` leaves the kind out)."""
    lines = []
    for each in records:
        envelope: dict[str, object] = {"untaped": "1", "record": each.model_dump(mode="json")}
        if kind is not None:
            envelope["kind"] = kind
        lines.append(json.dumps(envelope))
    return "\n".join(lines) + "\n"
