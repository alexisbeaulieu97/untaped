"""The ``inputs`` and ``outputs`` sections: the files commands read and the rows they print.

An input is a model a command reads through ``File[M]`` (its starter is a
command writing it, ``Annotated[File[M], Written]``); an output is a model a
command prints through ``Output[R]``. Each is addressed by its kind's noun
(``hello.salutation`` is ``untaped docs hello outputs salutation``) and
shows its model's docstring and one fields table per level.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from untaped.core.docs._docstring import describe, markdown
from untaped.core.docs._site import ORGANIZED, Site
from untaped.runtime.declaration import CommandDecl, FieldDecl, KindDecl

type Section = Literal["inputs", "outputs"]


def noun(kind: KindDecl) -> str:
    """The kind's last part, which its address ends in: ``test_result``."""
    return kind.kind.partition(".")[2]


def address(kind: KindDecl, section: Section) -> str:
    return f"untaped docs {kind.plugin} {section} {noun(kind)}"


def kinds(site: Site, name: str, section: Section) -> list[KindDecl]:
    return site.inputs(name) if section == "inputs" else site.outputs(name)


def find(site: Site, name: str, section: Section, word: str) -> KindDecl | None:
    return next((kind for kind in kinds(site, name, section) if noun(kind) == word), None)


# --- the lists ---------------------------------------------------------------------


def list_page(site: Site, name: str, section: Section) -> list[str]:
    """``untaped docs PLUGIN inputs|outputs``: each model's line and who reads or prints it."""
    lines = [*site.header(f"{name} {section}", name), ORGANIZED, ""]
    if section == "inputs":
        lines.append(
            f"Files you write for {name} commands. One input: `untaped docs {name} inputs NAME`."
        )
    else:
        lines.append(f"Rows {name} commands print. One output: `untaped docs {name} outputs NAME`.")
    lines.append("")
    for kind in kinds(site, name, section):
        lines.append(f"- `{noun(kind)}`: {markdown(kind.summary)}")
        lines += [f"  {line}" for line in _who(site, kind, section)]
    return lines


def _who(site: Site, kind: KindDecl, section: Section) -> list[str]:
    if section == "outputs":
        return [f"Printed by: {_listed(site, site.printers(kind.kind))}."]
    found = []
    readers = site.readers(kind.kind)
    if readers:
        found.append(f"Read by: {_listed(site, readers)}.")
    writers = site.writers(kind.kind)
    if writers:
        found.append(f"Starter: {_listed(site, writers)}.")
    return found


def _listed(site: Site, commands: Sequence[CommandDecl]) -> str:
    return ", ".join(f"`{site.line(command)}`" for command in commands)


# --- one model -----------------------------------------------------------------------


def page(site: Site, kind: KindDecl, section: Section) -> list[str]:
    """An input's or output's page: its prose, who reads or prints it, and its fields."""
    lines = [*site.header(f"{kind.plugin} {section} {noun(kind)}", kind.plugin), ORGANIZED]
    described = describe(kind.doc)
    lines += [part for text in (described.summary, described.body) if text for part in ("", text)]
    lines += ["", f"Kind: `{kind.kind}`.", *_who(site, kind, section)]
    lines += ["", "## Fields", *tables(kind.fields)]
    if described.examples:
        lines += ["", "## Examples", "", described.examples]
    return lines


def tables(fields: tuple[FieldDecl, ...], path: str = "") -> list[str]:
    """One table per level: ``fields``, then each nested model's under its key path."""
    if not fields:
        return ["", "No fields."]
    lines = ["", "| Field | Type | Description |", "| --- | --- | --- |"]
    lines += [f"| `{f.name}` | {_shape(f)} | {_cell(f.description)} |" for f in fields]
    for each in fields:
        if each.fields:
            key = f"{path}{each.name}{each.holds}"
            lines += ["", f"### `{key}`", *tables(each.fields, f"{key}.")]
    return lines


def field_lines(fields: tuple[FieldDecl, ...], where: str) -> list[str]:
    """A model's fields as a command's page lists them: its top level, and where the rest is."""
    lines = [f"- `{each.name}` ({_shape(each)}){_described(each)}" for each in fields]
    if any(each.fields for each in fields):
        lines.append(f"- Nested fields: `{where}`.")
    return lines


def _described(field: FieldDecl) -> str:
    return f": {markdown(field.description)}" if field.description else ""


def _shape(field: FieldDecl) -> str:
    parts = [field.type]
    if field.required:
        parts.append("required")
    elif not _unset(field.default):
        parts.append(f"default `{_default(field.default)}`")
    return ", ".join(parts)


def _unset(value: object) -> bool:
    """A default that says nothing: none, off, empty (``0`` is a value and shows)."""
    return value is None or value is False or value in ("", [], {})


def _default(value: object) -> str:
    if value is True:
        return "true"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _cell(text: str) -> str:
    return markdown(" ".join(text.split())).replace("|", "\\|")
