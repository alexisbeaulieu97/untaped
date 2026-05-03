"""Pin the typed boundary at ``untaped_awx.domain.payloads``.

``ResourceClient`` reads return :class:`ServerRecord`; writes accept
:class:`WritePayload` / :class:`ActionPayload`. The dict-style access
methods on ``ServerRecord`` reach into ``__pydantic_extra__``, which is
Pydantic v2's documented attribute for ``extra="allow"`` fields. These
tests pin the public contract so a Pydantic v3 rename fails fast at the
boundary instead of cascading into integration runs.

The docstring at ``payloads.py:34`` explicitly recommends a test named
``test_server_record_dict_access``; the cases below cover that name and
the surrounding contract.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from untaped_awx.domain.payloads import ActionPayload, ServerRecord, WritePayload


def _record(**fields: Any) -> ServerRecord:
    return ServerRecord(**fields)


def test_server_record_dict_access() -> None:
    """Round-trip the public dict-style API across declared and extra fields."""
    record = _record(id=42, name="prod-jt", organization=1, description="x")

    # Declared fields via __getitem__
    assert record["id"] == 42
    assert record["name"] == "prod-jt"
    # Extra fields via __getitem__ (stored in __pydantic_extra__)
    assert record["organization"] == 1
    assert record["description"] == "x"

    # get() mirrors dict.get for both declared and extra fields
    assert record.get("id") == 42
    assert record.get("organization") == 1
    # Default returned only when the key is absent
    assert record.get("missing", "fallback") == "fallback"
    assert record.get("missing") is None


def test_server_record_getitem_keyerror_for_absent_key() -> None:
    record = _record(id=1)
    with pytest.raises(KeyError):
        _ = record["nope"]


def test_server_record_get_present_but_none_returns_none_not_default() -> None:
    """The declared ``name: str | None`` field carefully distinguishes
    "absent" (default) from "present but None" — the docstring at
    ``payloads.py:46-54`` warns callers about this. Verify the contract.
    """
    record = _record(id=1)  # name omitted → defaults to None on the model
    # `name` is declared, so it's "present" with value None — get() returns
    # None, not the caller's default.
    assert record.get("name", "fallback") is None


def test_server_record_contains() -> None:
    record = _record(id=1, name="x", organization=7)

    assert "id" in record  # declared
    assert "organization" in record  # extra
    assert "missing" not in record
    # Non-string keys must not be considered contained
    assert 1 not in record  # type: ignore[operator]


def test_server_record_is_frozen() -> None:
    record = _record(id=1, organization=7)
    with pytest.raises(ValidationError):
        record.id = 99  # type: ignore[misc]


def test_server_record_model_dump_round_trip() -> None:
    """The strategy bridge path (per AGENTS.md `Typed boundary`): a
    ``ServerRecord`` from a read is flattened via ``model_dump()`` for the
    apply pipeline's strip / diff / preserve passes. The dict must include
    extras."""
    record = _record(id=1, name="thing", organization=7, custom_field="val")
    dumped = record.model_dump()
    assert dumped["id"] == 1
    assert dumped["name"] == "thing"
    assert dumped["organization"] == 7
    assert dumped["custom_field"] == "val"


def test_write_and_action_payload_accept_arbitrary_fields_and_are_frozen() -> None:
    """``WritePayload`` and ``ActionPayload`` are intentionally empty
    BaseModels with ``extra="allow"`` and ``frozen=True``. Smoke-test
    that arbitrary fields round-trip and that mutation is refused."""
    write = WritePayload(name="x", inventory=1)  # type: ignore[call-arg]
    action = ActionPayload(extra_vars={"k": "v"})  # type: ignore[call-arg]

    assert write.model_dump() == {"name": "x", "inventory": 1}
    assert action.model_dump() == {"extra_vars": {"k": "v"}}

    with pytest.raises(ValidationError):
        write.name = "y"  # type: ignore[misc, attr-defined]
    with pytest.raises(ValidationError):
        action.extra_vars = {}  # type: ignore[misc, attr-defined]
