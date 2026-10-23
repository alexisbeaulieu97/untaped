"""Tree output: a graph drawn as an indented tree, for any plugin.

A plugin builds a :class:`TreeGraph` (a root, nodes and edges by key) and
prints it with :func:`print_tree`, or gets its plain text with
:func:`tree_text`. The renderer walks the graph itself, so shared branches
and cycles are found by key: a branch drawn again is numbered ``[n]`` where
it is drawn in full, and its later occurrences hold one ``… see [n]`` line
instead of their branches; a node already on the path from the root is
marked ``↻ cycle``. Glyphs follow the theme's border (ASCII for ``ascii``)
and each part is styled from the theme's color roles.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Literal, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, model_validator
from rich.cells import cell_len

from untaped.sdk._ui import UiContext, ui_context
from untaped.sdk.marks import experimental

if TYPE_CHECKING:
    from rich.text import Text


@experimental
class TreeGraphNode(BaseModel):
    """One node of a :class:`TreeGraph`, found by ``key`` wherever it is drawn.

    ``notes`` follow the label, dim, in one aligned column. ``stopped`` marks
    a node whose branches were not read: the row ends in ``…``, and a reason
    (``depth limit``) adds the note ``not read: <reason>``. ``tone`` styles the label:
    ``error`` in the theme's error color, ``strong`` (a node to notice) in
    its success color, bold.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    label: str
    notes: tuple[str, ...] = ()
    stopped: bool | str = False
    tone: Literal["plain", "error", "strong"] = "plain"


@experimental
class TreeGraphEdge(BaseModel):
    """``child`` drawn under ``parent`` (keys), in ``section``.

    ``notes`` follow the child's own notes; ``suffix`` is drawn dim right
    after the child's label (``(2 nodes)``), before its marks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    parent: str
    child: str
    notes: tuple[str, ...] = ()
    suffix: str = ""
    section: str | None = None


@experimental
class TreeGraph(BaseModel):
    """A graph to draw as a tree from ``root``.

    The first line is the root's label and the graph's ``notes``; the
    root's own ``notes`` show where it is drawn again
    (a cycle). Sections come in the order their first edge does: an untitled
    one right under the first line, a titled one after a blank line and its
    title. Siblings are drawn in edge order. ``summary`` is the last line,
    after a blank one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: TreeGraphNode
    notes: tuple[str, ...] = ()
    nodes: tuple[TreeGraphNode, ...] = ()
    edges: tuple[TreeGraphEdge, ...] = ()
    summary: str | None = None

    @model_validator(mode="after")
    def _check_keys(self) -> Self:
        keys = [self.root.key, *(node.key for node in self.nodes)]
        if duplicated := sorted(key for key, count in Counter(keys).items() if count > 1):
            raise ValueError(f"duplicate node keys: {', '.join(duplicated)}")
        known = set(keys)
        if unknown := sorted(
            {key for edge in self.edges for key in (edge.parent, edge.child)} - known
        ):
            raise ValueError(f"edges name unknown node keys: {', '.join(unknown)}")
        return self

    def cycle_edges(self) -> tuple[TreeGraphEdge, ...]:
        """The edges drawn ``↻ cycle``, in drawing order."""
        return tuple(
            row.edge for _, rows in _section_rows(self) for row in rows if row.marker == "cycle"
        )


@experimental
def print_tree(graphs: TreeGraph | Sequence[TreeGraph], *, ui: UiContext | None = None) -> None:
    """Print ``graphs`` to stdout in one write, separated by a blank line (none: nothing).

    Color only where stdout supports it; a line too wide for the terminal
    ends in an ellipsis, and piped output is never cut. ``ui=None`` uses the
    settings-resolved theme.
    """
    from rich.text import Text  # noqa: PLC0415 — Rich styling only when printing

    ui = ui or ui_context()
    lines = _lines(graphs, _glyphs(ui))
    if not lines:
        return
    styles = {
        role: (ui.theme.color_roles.get(theme_role) if theme_role else None) or default or ""
        for role, (theme_role, default) in _ROLE_STYLES.items()
    }
    text: Text = Text("\n").join(
        Text.assemble(*((segment.text, styles[segment.role]) for segment in line)) for line in lines
    )
    ui.styled(text, truncate=True)


@experimental
def tree_text(graphs: TreeGraph | Sequence[TreeGraph], *, ui: UiContext | None = None) -> str:
    """The lines :func:`print_tree` prints, as plain text (for a file)."""
    return "\n".join(
        "".join(segment.text for segment in line)
        for line in _lines(graphs, _glyphs(ui or ui_context()))
    )


_Role = Literal["target", "section", "guide", "node", "error", "strong", "note", "ref", "cycle"]

_ROLE_STYLES: dict[_Role, tuple[str | None, str | None]] = {
    "target": (None, "bold"),
    "section": ("header", "bold"),
    "guide": ("border", "dim"),
    "node": ("value", None),
    "error": ("error", "red"),
    "strong": ("success", "bold green"),
    "note": (None, "dim"),
    "ref": ("key", "cyan"),
    "cycle": ("warning", "yellow"),
}
"""Per role: the theme color role that styles it, and the style used when the theme sets none."""


class _Segment(NamedTuple):
    text: str
    role: _Role


type _Line = tuple[_Segment, ...]


class _Glyphs(NamedTuple):
    branch: str
    last: str
    pipe: str
    cycle: str
    stopped: str


_UNICODE = _Glyphs(branch="├── ", last="└── ", pipe="│   ", cycle="↻ cycle", stopped="…")
_ASCII = _Glyphs(branch="|-- ", last="`-- ", pipe="|   ", cycle="(cycle)", stopped="...")
_BLANK = "    "
"""The guide below a last child: as wide as the connectors, in any glyph set."""

_NOTE_COLUMN_MAX = 56
"""Notes line up after the widest row up to this column, then follow two spaces."""


def _glyphs(ui: UiContext) -> _Glyphs:
    return _ASCII if ui.theme.border == "ascii" else _UNICODE


class _Row(NamedTuple):
    """One drawn line under the first: whether it and each ancestor is a last child, its edge.

    ``repeat`` is a branch drawn earlier in the section, and ``see`` the
    placeholder line under it that points back to that branch.
    """

    lasts: tuple[bool, ...]
    edge: TreeGraphEdge
    marker: Literal["cycle", "repeat", "see"] | None = None


def _section_rows(graph: TreeGraph) -> list[tuple[str | None, list[_Row]]]:
    """Each section's depth-first rows, a branch expanded once per section."""
    sections: dict[str | None, dict[str, list[TreeGraphEdge]]] = {}
    for edge in graph.edges:
        sections.setdefault(edge.section, {}).setdefault(edge.parent, []).append(edge)
    return [
        (title, rows)
        for title, children in sections.items()
        if (rows := _walk(graph.root.key, children))
    ]


def _walk(root: str, children: dict[str, list[TreeGraphEdge]]) -> list[_Row]:
    rows: list[_Row] = []
    drawn: set[str] = set()
    on_path = {root}
    # Each frame: the rows' ancestors' last flags, the node whose children it walks, and those.
    stack: list[tuple[tuple[bool, ...], str, Iterator[tuple[bool, TreeGraphEdge]]]]
    stack = [((), root, _with_last(children.get(root, [])))]
    while stack:
        lasts, parent, entries = stack[-1]
        entry = next(entries, None)
        if entry is None:
            stack.pop()
            on_path.discard(parent)
            continue
        is_last, edge = entry
        row_lasts = (*lasts, is_last)
        if edge.child in on_path:
            rows.append(_Row(row_lasts, edge, "cycle"))
            continue
        if edge.child in drawn and children.get(edge.child):
            rows.append(_Row(row_lasts, edge, "repeat"))
            rows.append(_Row((*row_lasts, True), edge, "see"))
            continue
        drawn.add(edge.child)
        rows.append(_Row(row_lasts, edge))
        if edge.child in children:
            on_path.add(edge.child)
            stack.append((row_lasts, edge.child, _with_last(children[edge.child])))
    return rows


def _with_last[T](items: Sequence[T]) -> Iterator[tuple[bool, T]]:
    return ((index == len(items) - 1, item) for index, item in enumerate(items))


def _lines(graphs: TreeGraph | Sequence[TreeGraph], glyphs: _Glyphs) -> list[_Line]:
    lines: list[_Line] = []
    for graph in [graphs] if isinstance(graphs, TreeGraph) else graphs:
        if lines:
            lines.append(())
        lines.extend(_graph_lines(graph, glyphs))
    return lines


class _Labelled(NamedTuple):
    """A row's guide, label and marks, their width in cells (``None``: not aligned), its notes."""

    segments: list[_Segment]
    width: int | None
    notes: list[str]


def _graph_lines(graph: TreeGraph, glyphs: _Glyphs) -> list[_Line]:
    """The first line, each section's rows, then the summary."""
    nodes = {node.key: node for node in (graph.root, *graph.nodes)}
    sections: list[tuple[str | None, list[_Labelled]]] = []
    numbered = 0
    for title, rows in _section_rows(graph):
        references = _number_references(rows, start=numbered + 1)
        numbered += len(references)
        sections.append((title, [_labelled(row, nodes, references, glyphs) for row in rows]))
    widths = [row.width for _, labelled in sections for row in labelled if row.width is not None]
    column = min(max(widths, default=0), _NOTE_COLUMN_MAX)
    lines: list[_Line] = [_first_line(graph, glyphs)]
    for title, labelled in sections:
        if title is not None:
            lines.extend([(), (_Segment(title, "section"),)])
        lines.extend(_row_line(row, column) for row in labelled)
    if graph.summary:
        lines.extend([(), (_Segment(graph.summary, "note"),)])
    return lines


def _first_line(graph: TreeGraph, glyphs: _Glyphs) -> _Line:
    root = graph.root
    line = [_Segment(root.label, "target")]
    if root.stopped:
        line.append(_Segment(f" {glyphs.stopped}", "note"))
    if notes := [*graph.notes, *_stop_note(root)]:
        line.append(_Segment(f"  {' · '.join(notes)}", "note"))
    return tuple(line)


def _number_references(rows: list[_Row], *, start: int) -> dict[str, int]:
    """Number, in drawing order from ``start``, every branch a later row refers back to."""
    referenced = {row.edge.child for row in rows if row.marker == "repeat"}
    first_rows = [
        row.edge.child for row in rows if row.marker is None and row.edge.child in referenced
    ]
    return {key: number for number, key in enumerate(first_rows, start=start)}


def _labelled(
    row: _Row, nodes: dict[str, TreeGraphNode], references: dict[str, int], glyphs: _Glyphs
) -> _Labelled:
    """Guide, label, the edge's suffix and the marks.

    A numbered branch's first row ends in ``[n]``, and the placeholder under
    a later occurrence says ``… see [n]``; a node already on the path takes
    the cycle mark. ``…`` follows a stopped node (not on a cycle row). Width
    is in terminal cells, so wide characters keep the notes aligned.
    """
    node = nodes[row.edge.child]
    *ancestors, is_last = row.lasts
    guide = _Segment(
        "".join(_BLANK if last else glyphs.pipe for last in ancestors)
        + (glyphs.last if is_last else glyphs.branch),
        "guide",
    )
    number = references.get(node.key)
    if row.marker == "see":
        return _Labelled([guide, _Segment(f"{glyphs.stopped} see [{number}]", "ref")], None, [])
    segments = [guide, _Segment(node.label, "node" if node.tone == "plain" else node.tone)]
    if row.edge.suffix:
        segments.append(_Segment(f" {row.edge.suffix}", "note"))
    if row.marker == "cycle":
        segments.append(_Segment(f" {glyphs.cycle}", "cycle"))
    elif row.marker is None and number is not None:
        segments.append(_Segment(f" [{number}]", "ref"))
    if node.stopped and row.marker != "cycle":
        segments.append(_Segment(f" {glyphs.stopped}", "note"))
    width = sum(cell_len(segment.text) for segment in segments)
    return _Labelled(segments, width, [*node.notes, *row.edge.notes, *_stop_note(node)])


def _row_line(row: _Labelled, column: int) -> _Line:
    if not row.notes:
        return tuple(row.segments)
    padding = " " * max(column - (row.width or 0), 0) + "  "
    return (*row.segments, _Segment(padding, "note"), _Segment(" · ".join(row.notes), "note"))


def _stop_note(node: TreeGraphNode) -> tuple[str, ...]:
    return (f"not read: {node.stopped}",) if isinstance(node.stopped, str) and node.stopped else ()
