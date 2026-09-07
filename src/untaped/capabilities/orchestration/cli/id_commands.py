"""Allocate caller-stable typed IDs."""

from __future__ import annotations

import random
import sys
import time
from typing import Literal
from uuid import UUID

from cyclopts import App

from untaped.capabilities.orchestration.cli.options import (
    ColumnsOption,
    OutputFormat,
    validate_format,
)
from untaped.capabilities.orchestration.cli.output import CommandResult, emit_encoded, encode_result


def _uuid7_hex() -> str:
    """Return a UUIDv7 hex string.

    Uses :func:`uuid.uuid7` on Python 3.14+; below that the name does not
    exist, so compose an RFC 9562 v7 value (48-bit unix-ms timestamp,
    version and variant bits, 74 random bits) to keep the Stage-B floor
    importable with no new 3.14-only debt.
    """
    try:
        from uuid import uuid7  # noqa: PLC0415
    except ImportError:
        unix_ms = time.time_ns() // 1_000_000
        value = (
            (unix_ms << 80)
            | (7 << 76)
            | (random.getrandbits(12) << 64)
            | (2 << 62)
            | random.getrandbits(62)
        )
        return UUID(int=value).hex
    return uuid7().hex


def register(app: App) -> None:
    ids = app.command(App(name="id", help="Allocate caller-stable typed IDs."))
    assert isinstance(ids, App)

    @ids.command(name="new")
    def new_id(
        kind: Literal["store", "task", "decision"],
        /,
        *,
        format: OutputFormat = "table",
        columns: ColumnsOption = (),
        debug: bool = False,
    ) -> None:
        del debug
        try:
            fmt = validate_format(format, allowed=("table", "json", "raw"))
        except ValueError as error:
            sys.stderr.write(f"error: {error}\n")
            raise SystemExit(2) from error
        if fmt == "raw" and columns:
            sys.stderr.write("error: id new --format raw does not accept --columns/-c\n")
            raise SystemExit(2)
        prefix = {"store": "sto", "task": "tsk", "decision": "dec"}[kind]
        item_id = f"{prefix}_{_uuid7_hex()}"
        emit_encoded(
            encode_result(
                CommandResult("id new", {"kind": kind, "id": item_id}),
                fmt=fmt,
                columns=columns,
            )
        )
