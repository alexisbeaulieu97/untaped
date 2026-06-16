"""Wire contract for the ``--format pipe`` interchange format.

The ``pipe`` output format emits one JSON object per line (NDJSON), each line
fully self-describing so it survives ``head``/``grep``/concatenation::

    {"untaped": "1", "kind": "github.repo", "record": {...}}

This module owns the contract (constants + parse/validate) with **no dependency
on the rendering layer**, so the producer (:mod:`untaped.ui`) and the consumer
(:mod:`untaped.stdin`) can both share it without an import cycle.

Record values are serialized with ``json.dumps(default=str)`` (same as
``--format json``), so non-JSON-native types (datetime, Decimal, enum) become
strings and do **not** round-trip to their original type — fidelity is
JSON-native types only.

Pipe envelope **v1** — frozen and stable across all ``untaped`` SDK 1.x
releases. Any change to the envelope shape is a major (2.0) SDK event. This
freeze is what lets independently-installed tools interoperate: each tool may
ship its own SDK version, but ``untaped-github | untaped-ansible`` is
guaranteed to work as long as both stay on SDK 1.x. See ``docs/decisions.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from untaped.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence

PIPE_MARKER_KEY = "untaped"
PIPE_ENVELOPE_VERSION = "1"
SUPPORTED_PIPE_VERSIONS = frozenset({"1"})


@dataclass(frozen=True)
class PipeEnvelope:
    """One decoded ``--format pipe`` line: a record plus its metadata.

    This is the **v1** envelope, frozen and stable across all ``untaped`` SDK
    1.x releases; any change to its shape is a major (2.0) SDK event so that
    independently-installed tools on different 1.x SDKs interoperate. See the
    module docstring and ``docs/decisions.md``.
    """

    kind: str | None
    record: dict[str, object]
    lineno: int


def is_envelope_line(obj: object) -> TypeGuard[dict[str, object]]:
    """True if a decoded JSON value looks like a pipe envelope.

    The marker key is the sole discriminator: a bare identifier that is valid
    JSON (``123``, ``"foo"``) decodes to a scalar, not a dict, and a repo slug
    like ``acme/api`` is not valid JSON at all — so only an actual envelope
    object trips this.
    """
    return isinstance(obj, dict) and PIPE_MARKER_KEY in obj


def parse_envelope_line(lineno: int, text: str) -> PipeEnvelope:
    """Decode and validate one envelope line, or raise a line-precise error."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"line {lineno}: invalid JSON: {exc.msg}") from exc
    if not is_envelope_line(obj):
        raise ConfigError(f"line {lineno}: not an untaped pipe record")
    version = obj.get(PIPE_MARKER_KEY)
    if version not in SUPPORTED_PIPE_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_PIPE_VERSIONS))
        raise ConfigError(
            f"line {lineno}: unsupported pipe version {version!r} (supported: {supported})"
        )
    record = obj.get("record")
    if not isinstance(record, dict):
        raise ConfigError(f"line {lineno}: record is not an object")
    kind = obj.get("kind")
    if kind is not None and not isinstance(kind, str):
        raise ConfigError(f"line {lineno}: kind must be a string or null")
    return PipeEnvelope(kind=kind, record=record, lineno=lineno)


def common_kind(envelopes: Sequence[PipeEnvelope]) -> str | None:
    """The shared ``kind`` across envelopes, or ``None`` if they differ or are empty."""
    kinds = {env.kind for env in envelopes}
    if len(kinds) == 1:
        return next(iter(kinds))
    return None
