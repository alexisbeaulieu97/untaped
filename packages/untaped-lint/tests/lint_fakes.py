"""Fake record kinds and rule providers for lint's engine and command tests.

Install them with ``install(LintPlugin, providers={"acme": [Acme]})``: the
``providers`` key is the fake plugin's name, so ``acme``'s ids read
``acme.<id>``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from untaped.sdk import InstalledPlugin, Record, kind_of
from untaped_lint.api import FileLocation, Finding, Linter, Rule, Rules, finding, rule
from untaped_lint.application.rules import rule_row


class Widget(Record, kind="linttest.widget"):
    """A record two of acme's rules read."""

    name: str


class Gadget(Record, kind="linttest.gadget"):
    """A record one of gizmo's rules reads."""

    name: str


class Sprocket(Record, kind="linttest.sprocket"):
    """A record no rule reads."""

    name: str


@rule(severity="error")
def wide(widget: Widget) -> Iterator[Finding]:
    """A widget is narrow."""
    yield finding(wide, widget.name, "too wide")


@rule(severity="warning")
def tall(widget: Widget) -> Iterator[Finding]:
    """A widget is short."""
    yield finding(tall, widget.name, "too tall")


@rule(severity="error")
def boom(widget: Widget) -> Iterator[Finding]:
    """A rule that raises."""
    raise RuntimeError(f"cannot read {widget.name}")


@rule(severity="error")
def default_branch(gadget: Gadget) -> Iterator[Finding]:
    """A gadget names its default branch."""
    yield finding(default_branch, gadget.name, "no default branch")


@rule(severity="warning")
def placed(widget: Widget) -> Iterator[Finding]:
    """A widget's lines are placed."""
    for line in (1, 2):
        location = FileLocation(path="src/a.py", line=line)
        yield finding(placed, widget.name, "misplaced", location=location)


class Placing(Rules):
    RULES = (placed,)


class Counting(Rules):
    """A ``Rules`` host that counts the records it is asked about, by kind."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def findings(self, record: Record) -> list[Finding]:
        self.asked.append(kind_of(type(record)) or "")
        return super().findings(record)


class Acme(Counting):
    RULES = (wide, tall)


class Gizmo(Counting):
    RULES = (default_branch,)


class Raising(Rules):
    RULES = (wide, boom)


class WarnOnly(Rules):
    RULES = (tall,)


class NotAProvider(Linter):
    """Fills ``Linter`` without being a ``Provider``: composition quarantines its plugin."""

    def rules(self) -> list[Rule]:
        return []

    def findings(self, record: Record) -> list[Finding]:
        return []


class Malformed(Linter):
    """Declares ``wide`` and answers one valid finding and one with a bad severity."""

    def rules(self) -> list[Rule]:
        return [rule_row(wide)]

    def findings(self, record: Record) -> list[Finding]:
        bad = Finding.model_construct(rule="wide", severity="fatal", target="b", message="m")
        return [finding(wide, "a", "too wide"), bad]


class MalformedRule(Linter):
    """Declares ``wide`` and a rule whose id is dotted, which the engine never prefixes."""

    def rules(self) -> list[Rule]:
        bad = Rule.model_construct(
            id="acme.dotted", reads=rule_row(wide).reads, severity="error", summary="Dotted."
        )
        return [bad, rule_row(wide)]

    def findings(self, record: Record) -> list[Finding]:
        return [finding(wide, "w", "too wide")]


@rule(severity="warning")
def seen(plugin: InstalledPlugin) -> Iterator[Finding]:
    """Records each plugin it reads."""
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
