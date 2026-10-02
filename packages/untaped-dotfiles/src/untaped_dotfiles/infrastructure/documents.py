"""Structured documents a ``merge`` file writes into: JSON and comment-keeping YAML.

JSON is rewritten with two-space indentation (programs that own such files,
editor and agent settings, write it that way too). YAML goes through
``ruamel.yaml``'s round-trip loader so the target's comments, order and
quoting survive a merge; ``ruamel.yaml`` is imported lazily.
"""

from __future__ import annotations

import io
import json
from collections.abc import MutableMapping
from typing import Any

import yaml

from untaped_dotfiles.domain.models import MergeFormat
from untaped_dotfiles.errors import DotfilesError


def load_document(text: str, *, fmt: MergeFormat, where: str) -> MutableMapping[str, Any]:
    """Parse ``text`` as a mapping; an empty document is an empty mapping."""
    if not text.strip():
        return _empty(fmt)
    try:
        raw: Any = json.loads(text) if fmt == "json" else _yaml().load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise DotfilesError(f"{where} is invalid {fmt.upper()}: {exc}", category="invalid") from exc
    except Exception as exc:  # ruamel raises its own error classes
        raise DotfilesError(f"{where} is invalid {fmt.upper()}: {exc}", category="invalid") from exc
    if raw is None:
        return _empty(fmt)
    if not isinstance(raw, MutableMapping):
        raise DotfilesError(f"{where} must contain a mapping", category="invalid")
    return raw


def dump_document(document: MutableMapping[str, Any], *, fmt: MergeFormat) -> str:
    if fmt == "json":
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    out = io.StringIO()
    _yaml().dump(document, out)
    return out.getvalue()


def plain(document: Any) -> Any:
    """``document`` as plain dicts, lists and scalars (hashable the same way everywhere)."""
    return json.loads(json.dumps(document, default=str))


def _empty(fmt: MergeFormat) -> MutableMapping[str, Any]:
    if fmt == "json":
        return {}
    from ruamel.yaml.comments import CommentedMap  # noqa: PLC0415

    return CommentedMap()


def _yaml() -> Any:
    from ruamel.yaml import YAML  # noqa: PLC0415

    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    rt.indent(mapping=2, sequence=4, offset=2)
    return rt
