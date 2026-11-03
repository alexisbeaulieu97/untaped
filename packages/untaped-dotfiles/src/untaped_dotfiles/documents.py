"""Structured documents a ``merge`` file writes into: JSON, and comment-keeping YAML and TOML.

JSON is rewritten with two-space indentation (programs that own such files,
editor and agent settings, write it that way too). YAML goes through
``ruamel.yaml``'s round-trip loader and TOML through ``tomlkit``, so the
target's comments, order, quoting and indentation survive a merge; both are
imported lazily.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping, MutableMapping
from typing import TYPE_CHECKING, Any, cast

from untaped.sdk import yaml_mapping_indent
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.hashing import value_hash
from untaped_dotfiles.merge import Fit, as_is, merge_into, remove_paths
from untaped_dotfiles.models import KeyPath
from untaped_dotfiles.package import MergeFormat

if TYPE_CHECKING:
    from tomlkit import TOMLDocument


def load_document(text: str, *, fmt: MergeFormat, where: str) -> MutableMapping[str, Any]:
    """Parse ``text`` as a mapping; an empty document is an empty mapping."""
    if not text.strip():
        return _empty(fmt)
    try:
        raw: Any = _parse(text, fmt)
    except Exception as exc:  # json, ruamel and tomlkit each raise their own classes
        raise DotfilesError(f"{where} is invalid {fmt.upper()}: {exc}", category="invalid") from exc
    if raw is None:
        return _empty(fmt)
    if not isinstance(raw, MutableMapping):
        raise DotfilesError(f"{where} must contain a mapping", category="invalid")
    return raw


def load_values(text: str, *, fmt: MergeFormat, where: str) -> dict[str, Any]:
    """Parse ``text`` into the values a merge writes, detached from the text.

    TOML values keep their types (a date stays a date; ``plain`` would make it a
    string) and leave the source's comments and layout behind: the target's
    layout is kept, the source's is not.
    """
    document = load_document(text, fmt=fmt, where=where)
    if fmt == "toml":
        return _unwrap(document)
    detached: dict[str, Any] = plain(document)
    return detached


def merge_text(
    text: str, incoming: Mapping[str, Any], *, fmt: MergeFormat, where: str
) -> tuple[MutableMapping[str, Any], str]:
    """Merge ``incoming`` into the document ``text`` holds: ``(merged document, its text)``.

    TOML first merges as JSON and YAML do, a new table getting its own
    ``[header]``. Where a header would capture the keys after it (under dotted
    keys such as ``a.b = 1``) the merge is redone with new tables and arrays of
    tables written inline. A TOML text is kept only when it reads back as the
    same merge on plain values; otherwise nothing is written.
    """
    if fmt != "toml":
        document = load_document(text, fmt=fmt, where=where)
        merge_into(document, incoming)
        return document, dump_document(document, fmt=fmt, where=where, like=text)
    return _edit_toml(
        text,
        lambda document, fit: merge_into(document, incoming, fit=fit),
        fits=(as_is, _inline_below_headers),
        where=where,
    )


def remove_text(text: str, managed: tuple[KeyPath, ...], *, fmt: MergeFormat, where: str) -> str:
    """The document ``text`` holds without the keys at ``managed`` (see :func:`remove_paths`).

    A TOML text is kept only when it reads back as the same removal on plain
    values; otherwise nothing is written.
    """
    if fmt != "toml":
        document = load_document(text, fmt=fmt, where=where)
        remove_paths(document, managed)
        return dump_document(document, fmt=fmt, where=where, like=text)
    _, rendered = _edit_toml(
        text, lambda document, fit: remove_paths(document, managed), fits=(as_is,), where=where
    )
    return rendered


def dump_document(
    document: MutableMapping[str, Any], *, fmt: MergeFormat, where: str, like: str = ""
) -> str:
    """Render ``document``; YAML is indented the way ``like`` (the original text) was.

    TOML is refused unless it reads back as ``document``, so a layout
    ``tomlkit`` can't edit never reaches the file.
    """
    if fmt == "json":
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if fmt == "toml":
        rendered = _toml_text(document, _unwrap(document))
        if rendered is None:
            raise _refused(where)
        return rendered
    out = io.StringIO()
    _yaml(like).dump(document, out)
    return out.getvalue()


def plain(document: Any) -> Any:
    """``document`` as plain dicts, lists and scalars (hashable the same way everywhere)."""
    return json.loads(json.dumps(document, default=str))


def _inline_below_headers(parent: MutableMapping[str, Any], value: Any) -> Any:
    """A table or array of tables, inline unless ``parent`` is the top or a ``[header]`` table."""
    from tomlkit import TOMLDocument  # noqa: PLC0415
    from tomlkit.items import Table  # noqa: PLC0415

    headed = isinstance(parent, TOMLDocument) or (
        isinstance(parent, Table) and not parent.is_super_table()
    )
    return value if headed else _inline(value)


def _inline(value: Any) -> Any:
    import tomlkit  # noqa: PLC0415

    if isinstance(value, Mapping):
        table = tomlkit.inline_table()
        for key, item in value.items():
            table[key] = _inline(item)
        return table
    if isinstance(value, list):
        array = tomlkit.array()
        array.extend(_inline(item) for item in value)
        return array
    return value


def _edit_toml(
    text: str,
    edit: Callable[[MutableMapping[str, Any], Fit], None],
    *,
    fits: tuple[Fit, ...],
    where: str,
) -> tuple[MutableMapping[str, Any], str]:
    """Apply ``edit`` to the TOML ``text`` with the first fit whose text reads back right.

    Right is what ``edit`` makes of the plain values: tomlkit can lose a key
    while editing (an array of tables split around its parent table), so the
    edited document is not its own reference.
    """
    expected = _unwrap(load_document(text, fmt="toml", where=where))
    edit(expected, as_is)
    failure: Exception | None = None
    for fit in fits:
        document = load_document(text, fmt="toml", where=where)
        try:
            edit(document, fit)
        except Exception as exc:  # tomlkit can't edit this layout with this fit; next one
            failure = exc
            continue
        rendered = _toml_text(document, expected)
        if rendered is not None:
            return document, rendered
    raise _refused(where) from failure


def _toml_text(document: MutableMapping[str, Any], expected: Any) -> str | None:
    """``document`` as TOML, or ``None`` when that text doesn't read back as ``expected``."""
    import tomlkit  # noqa: PLC0415

    text = tomlkit.dumps(document)
    try:
        back = tomlkit.parse(text).unwrap()
    except Exception:  # tomlkit wrote text it can't read
        return None
    return text if value_hash(back) == value_hash(expected) else None


def _unwrap(document: MutableMapping[str, Any]) -> dict[str, Any]:
    """A TOML document's values as plain Python (dates stay dates)."""
    return cast("TOMLDocument", document).unwrap()


def _refused(where: str) -> DotfilesError:
    return DotfilesError(
        f"could not update {where}: tomlkit can't write its layout without moving "
        "keys; nothing was written",
        hint="write the target's tables as [headers] instead of dotted keys, then apply again",
        category="invalid",
    )


def _parse(text: str, fmt: MergeFormat) -> Any:
    if fmt == "json":
        return json.loads(text)
    if fmt == "toml":
        import tomlkit  # noqa: PLC0415

        return tomlkit.parse(text)
    return _yaml(text).load(text)


def _empty(fmt: MergeFormat) -> MutableMapping[str, Any]:
    if fmt == "json":
        return {}
    if fmt == "toml":
        import tomlkit  # noqa: PLC0415

        return tomlkit.document()
    from ruamel.yaml.comments import CommentedMap  # noqa: PLC0415

    return CommentedMap()


def _yaml(like: str) -> Any:
    """A round-trip loader/dumper indented like ``like`` (default: 2 spaces, ``-`` under key)."""
    from ruamel.yaml import YAML  # noqa: PLC0415
    from ruamel.yaml.util import load_yaml_guess_indent  # noqa: PLC0415

    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    sequence, offset = 2, 0
    if like.strip():
        try:
            _, guessed_sequence, guessed_offset = load_yaml_guess_indent(like)
        except Exception:  # unparsable text: the caller reports it when loading
            pass
        else:
            if guessed_sequence is not None and guessed_offset is not None:
                sequence, offset = guessed_sequence, guessed_offset
    rt.indent(mapping=yaml_mapping_indent(like), sequence=sequence, offset=offset)
    return rt
