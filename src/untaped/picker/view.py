"""Render a :class:`~untaped.picker.state.PickerState` as styled text fragments.

The output is prompt_toolkit ``StyleAndTextTuples`` (``(style, text)`` pairs)
built from plain tuples, so this module never imports prompt_toolkit. Panes
sit side by side from :data:`WIDE` columns and stack below that.
"""

# ruff: noqa: RUF001  (the pane glyphs are intentional)

from __future__ import annotations

from untaped.picker.state import (
    ALL,
    CREATE,
    PickerState,
    Row,
    completions,
    is_inherited,
    rows,
    setting_value,
    visible,
)

Fragment = tuple[str, str]
Line = list[Fragment]

LIST_ROWS = 10
"""Visible rows in each pane's list."""
WIDE = 100
"""Terminal width from which the panes sit side by side."""

_KEYS = (
    "space toggle · / search · tab pane · enter edit · ←→ change · "
    "ctrl-s create · ctrl-r refresh · ctrl-c quit"
)


def render(state: PickerState, width: int) -> list[Fragment]:
    """The whole picker as fragments, lines separated by ``"\\n"``."""
    width = max(width, 40)
    lines: list[Line] = [_header(state, width), []]
    if width >= WIDE:
        left_width = width // 2
        left = _pane(
            "Search", _left_body(state, left_width - 4), left_width, state.focus != "selected"
        )
        right = _pane(
            f"Selected {len(state.selected)}",
            _right_body(state, width - left_width - 4),
            width - left_width,
            state.focus == "selected",
        )
        lines.extend(a + b for a, b in zip(left, right, strict=True))
    else:
        lines.extend(
            _pane("Search", _left_body(state, width - 4), width, state.focus != "selected")
        )
        lines.extend(
            _pane(
                f"Selected {len(state.selected)}",
                _right_body(state, width - 4),
                width,
                state.focus == "selected",
            )
        )
    lines.append(_footer(state, width))
    out: list[Fragment] = []
    for index, line in enumerate(lines):
        if index:
            out.append(("", "\n"))
        out.extend(line)
    return out


# --- layout helpers ---------------------------------------------------------


def _length(line: Line) -> int:
    return sum(len(text) for _style, text in line)


def _fit(line: Line, width: int) -> Line:
    """Truncate (with ``…``) or pad ``line`` to exactly ``width`` characters."""
    out: Line = []
    used = 0
    for style, text in line:
        room = width - used
        if room <= 0:
            break
        if len(text) > room:
            out.append((style, text[: room - 1] + "…"))
            used = width
            break
        out.append((style, text))
        used += len(text)
    if used < width:
        out.append(("", " " * (width - used)))
    return out


def _pane(title: str, body: list[Line], width: int, focused: bool) -> list[Line]:
    border = "class:picker.border.focus" if focused else "class:picker.border"
    inner = width - 4
    top_label = f" {title} "
    top: Line = [
        (border, "╭─"),
        (border, top_label),
        (border, "─" * max(0, width - 4 - len(top_label)) + "─╮"),
    ]
    lines = [_fit(top, width)]
    for line in body:
        lines.append([(border, "│ "), *_fit(line, inner), (border, " │")])
    lines.append([(border, "╰" + "─" * (width - 2) + "╯")])
    return lines


# --- header and footer ------------------------------------------------------


def _header(state: PickerState, width: int) -> Line:
    line: Line = [("class:picker.mark", " ◆ "), ("class:picker.heading", state.request.heading)]
    if state.request.title_label or state.title:
        shown = state.title or state.request.title_label
        style = "class:picker.value" if state.title else "class:picker.dim"
        line += [("", "  "), (style, shown)]
        if state.focus == "title":
            line.append(("class:picker.cursor", "█"))
    if state.request.subtitle is not None:
        subtitle = state.request.subtitle(state.title, state.defaults)
        gap = width - _length(line) - len(subtitle) - 1
        if gap >= 2:
            line += [("", " " * gap), ("class:picker.subtitle", subtitle)]
    return _fit(line, width)


def _footer(state: PickerState, width: int) -> Line:
    if state.quitting:
        return _fit(
            [("class:picker.error", f" discard {len(state.selected)} selected? y/n")], width
        )
    if state.error:
        return _fit([("class:picker.error", f" {state.error}")], width)
    return _fit([("class:picker.keys", f" {_KEYS}")], width)


# --- left pane --------------------------------------------------------------


def _left_body(state: PickerState, inner: int) -> list[Line]:
    search: Line = [("class:picker.dim", "/ "), ("", state.query)]
    if state.focus == "search":
        search.append(("class:picker.cursor", "█"))
    ranked = visible(state)
    cursor = min(state.cursor, max(0, len(ranked) - 1))
    start = max(0, min(cursor - LIST_ROWS // 2, len(ranked) - LIST_ROWS))
    body: list[Line] = [search]
    for index in range(start, start + LIST_ROWS):
        if index >= len(ranked):
            body.append([])
            continue
        entry = ranked[index]
        pointer = "› " if state.focus == "list" and index == cursor else "  "
        marked = entry.item.id in state.selected
        line: Line = [
            ("class:picker.cursor", pointer),
            ("class:picker.mark" if marked else "class:picker.dim", "◉ " if marked else "○ "),
        ]
        base = "class:picker.dim" if entry.item.dimmed else ""
        for position, char in enumerate(entry.item.label):
            line.append(
                (f"{base} class:picker.match" if position in entry.positions else base, char)
            )
        if entry.item.description:
            line += [("", "  "), ("class:picker.dim", entry.item.description)]
        body.append(line)
    status = f"{len(ranked)}" + (f" · {state.note}" if state.note else "")
    if state.refreshing:
        status += " · refreshing…"
    body.append([("class:picker.dim", status.rjust(inner))])
    return body


# --- right pane -------------------------------------------------------------


def _right_body(state: PickerState, inner: int) -> list[Line]:
    all_rows = rows(state)
    body: list[Line] = []
    for row in all_rows[:-1]:
        body.append(_right_row(state, row))
    window = LIST_ROWS + 1
    if len(body) > window:
        current = all_rows.index(state.row) if state.row in all_rows else 0
        start = max(0, min(current - window // 2, len(body) - window))
        body = body[start : start + window]
    body += [[] for _ in range(window - len(body))]
    create_focus = state.focus == "selected" and state.row == (CREATE, None)
    button = "class:picker.button.focus" if create_focus else "class:picker.button"
    body.append([("", " " * max(0, (inner - 10) // 2)), (button, "[ Create ]")])
    return body


def _right_row(state: PickerState, row: Row) -> Line:
    owner, key = row
    here = state.focus == "selected" and state.row == row
    pointer: Fragment = ("class:picker.cursor", "› " if here else "  ")
    if key is None:
        label = "all items" if owner == ALL else state.known[owner].label
        arrow = "▾ " if state.row[0] == owner else "▸ "
        summary = " · ".join(_shown(state, owner, s.key) for s in state.request.settings)
        return [
            pointer,
            ("class:picker.dim", arrow),
            ("", label),
            ("", "  "),
            ("class:picker.dim", summary),
        ]
    setting = next(s for s in state.request.settings if s.key == key)
    line: Line = [pointer, ("", "    "), ("class:picker.dim", f"{setting.label:<8}")]
    if here and state.editing is not None:
        hints = "  ".join(completions(state)[:4])
        return [
            *line,
            ("class:picker.value", state.editing),
            ("class:picker.cursor", "█"),
            ("", "  "),
            ("class:picker.dim", hints),
        ]
    value = _shown(state, owner, key)
    if is_inherited(state, owner, key):
        return [*line, ("class:picker.dim", f"· inherit ({value})")]
    if setting.choices:
        return [
            *line,
            ("class:picker.dim", "‹ "),
            ("class:picker.value", value),
            ("class:picker.dim", " ›"),
        ]
    return [*line, ("class:picker.value", value)]


def _shown(state: PickerState, owner: str, key: str) -> str:
    value = setting_value(state, owner, key)
    if value:
        return value
    setting = next(s for s in state.request.settings if s.key == key)
    return setting.placeholder or "—"
