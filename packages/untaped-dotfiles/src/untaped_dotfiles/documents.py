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
from collections.abc import MutableMapping
from typing import Any

from untaped.sdk import yaml_mapping_indent
from untaped_dotfiles.errors import DotfilesError
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


def dump_document(document: MutableMapping[str, Any], *, fmt: MergeFormat, like: str = "") -> str:
    """Render ``document``; YAML is indented the way ``like`` (the original text) was."""
    if fmt == "json":
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if fmt == "toml":
        import tomlkit  # noqa: PLC0415

        return tomlkit.dumps(document)
    out = io.StringIO()
    _yaml(like).dump(document, out)
    return out.getvalue()


def plain(document: Any) -> Any:
    """``document`` as plain dicts, lists and scalars (hashable the same way everywhere)."""
    return json.loads(json.dumps(document, default=str))


def values(document: MutableMapping[str, Any], *, fmt: MergeFormat) -> dict[str, Any]:
    """``document``'s values detached from its text: what a merge writes into another one.

    A TOML document keeps its value types (a date stays a date, where ``plain``
    would write it as a string) and leaves its comments and layout behind.
    """
    if fmt == "toml":
        from tomlkit import TOMLDocument  # noqa: PLC0415

        if isinstance(document, TOMLDocument):
            return document.unwrap()
    detached: dict[str, Any] = plain(document)
    return detached


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
