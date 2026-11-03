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
from collections.abc import Mapping, MutableMapping
from typing import Any

from untaped.sdk import yaml_mapping_indent
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.hashing import value_hash
from untaped_dotfiles.merge import Fit, as_is
from untaped_dotfiles.package import MergeFormat


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
        unwrapped: dict[str, Any] = document.unwrap()  # type: ignore[attr-defined]
        return unwrapped
    detached: dict[str, Any] = plain(document)
    return detached


def fit_for(fmt: MergeFormat) -> Fit:
    """How a merge shapes a value it writes into a document of ``fmt``.

    In TOML a new table or array of tables gets its own ``[header]`` only at the
    top or under a ``[header]`` table; under dotted keys (``a.b = 1``), an
    implicit or split table, or an inline table it is written inline, where a
    header would capture the keys after it.
    """
    return _toml_fit if fmt == "toml" else as_is


def dump_document(
    document: MutableMapping[str, Any], *, fmt: MergeFormat, where: str, like: str = ""
) -> str:
    """Render ``document``; YAML is indented the way ``like`` (the original text) was.

    TOML is read back before it is returned, and refused unless it reads back
    as ``document``, so a layout ``tomlkit`` can't edit never reaches the file.
    """
    if fmt == "json":
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if fmt == "toml":
        return _dump_toml(document, where=where)
    out = io.StringIO()
    _yaml(like).dump(document, out)
    return out.getvalue()


def plain(document: Any) -> Any:
    """``document`` as plain dicts, lists and scalars (hashable the same way everywhere)."""
    return json.loads(json.dumps(document, default=str))


def _toml_fit(parent: MutableMapping[str, Any], value: Any) -> Any:
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


def _dump_toml(document: MutableMapping[str, Any], *, where: str) -> str:
    import tomlkit  # noqa: PLC0415

    text = tomlkit.dumps(document)
    try:
        back = tomlkit.parse(text).unwrap()
    except Exception:  # tomlkit wrote text it can't read: refused below
        back = None
    if back is None or value_hash(back) != value_hash(document.unwrap()):  # type: ignore[attr-defined]
        raise DotfilesError(
            f"could not merge into {where}: its layout would move keys the source "
            "doesn't name; nothing was written",
            hint="write the target's tables as [headers] instead of dotted keys, then apply again",
            category="invalid",
        )
    return text


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
